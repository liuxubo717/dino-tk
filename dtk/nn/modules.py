import torch
import torch.nn as nn
from .utils import same_padding
from torchvision.models.resnet import BasicBlock, Bottleneck, conv1x1


class VideoDownsizer():
    def __init__(self, new_size):
        super(VideoDownsizer, self).__init__()
        self.new_size = new_size
        self.resizer = nn.AdaptiveAvgPool2d(new_size)

    def forward(self, x):
        old_size = x.size()
        new_size = list(x.size())
        new_size[-2], new_size[-1] = self.new_size[0], self.new_size[1]

        return self.resizer(x.view(-1, old_size[-2], old_size[-2])).view(new_size)


class MLP(nn.Module):
    def __init__(self, input_size, output_size, hidden=[128], batch_norm=True):
        super(MLP, self).__init__()

        self.layers = nn.ModuleList()
        layer_input = [input_size]
        layer_input.extend(hidden)

        for i in range(0, len(layer_input) - 1):
            if batch_norm:
                self.layers.append(nn.Sequential(nn.Linear(layer_input[i], layer_input[i + 1]),
                                                 nn.BatchNorm1d(layer_input[i + 1]),
                                                 nn.ReLU(True)))
            else:
                self.layers.append(nn.Sequential(nn.Linear(layer_input[i], layer_input[i + 1]),
                                                 nn.ReLU(True)))

        self.layers.append(nn.Linear(layer_input[-1], output_size))

    def forward(self, x):
        for layer in self.layers:
            x = layer(x)

        return x


class MedianPool1d(nn.Module):
    def __init__(self, kernel=3, stride=1, padding=0):
        super(MedianPool1d, self).__init__()
        self.kernel = kernel
        self.stride = stride
        self.pad = torch.nn.ReflectionPad1d(padding)

    def forward(self, x):
        x = self.pad(x)
        x = x.unfold(2, self.kernel, self.stride)
        return x.median(dim=-1)[0]


class UnetBlock2D(nn.Module):
    def __init__(self, in_channels, out_channels, skip_channels, in_size, kernel_size, stride=1, batch_norm=True):
        super(UnetBlock2D, self).__init__()
        # This ensures that we have same padding no matter if we have even or odd kernels
        padding = same_padding(kernel_size, stride)
        self.dcl1 = nn.ConvTranspose2d(in_channels + skip_channels, in_channels, 3, padding=1, bias=False)
        self.dcl2 = nn.ConvTranspose2d(in_channels, out_channels, kernel_size, stride=stride,
                                       padding=padding // 2, bias=False)
        if batch_norm:
            self.activation1 = nn.Sequential(nn.BatchNorm2d(in_channels), nn.ReLU(True))
            self.activation2 = nn.Sequential(nn.BatchNorm2d(out_channels), nn.ReLU(True))
        else:
            self.activation1 = nn.ReLU(True)
            self.activation2 = nn.ReLU(True)

        self.required_channels = out_channels
        self.out_size_required = tuple(x * stride for x in in_size)

    def forward(self, x, s):
        s = s.view(x.size())

        x = torch.cat([x, s], 1)

        x = self.dcl1(x)
        x = self.activation1(x)

        x = self.dcl2(x, output_size=[-1, self.required_channels, self.out_size_required[0], self.out_size_required[1]])
        x = self.activation2(x)
        return x


class UnetBlock1D(nn.Module):
    def __init__(self, in_channels, out_channels, skip_channels, in_size, kernel_size, stride=1, batch_norm=True):
        super(UnetBlock1D, self).__init__()
        # This ensures that we have same padding no matter if we have even or odd kernels
        padding = same_padding(kernel_size, stride)
        self.dcl1 = nn.ConvTranspose1d(in_channels + skip_channels, in_channels, 3, padding=1, bias=False)
        self.dcl2 = nn.ConvTranspose1d(in_channels, out_channels, kernel_size, stride=stride,
                                       padding=padding // 2, bias=False)
        if batch_norm:
            self.activation1 = nn.Sequential(nn.BatchNorm1d(in_channels), nn.ReLU(True))
            self.activation2 = nn.Sequential(nn.BatchNorm1d(out_channels), nn.ReLU(True))
        else:
            self.activation1 = nn.ReLU(True)
            self.activation2 = nn.ReLU(True)

        self.required_channels = out_channels
        self.out_size_required = in_size * stride

    def forward(self, x, s):
        s = s.view(x.size())
        x = torch.cat([x, s], 1)

        x = self.dcl1(x)
        x = self.activation1(x)

        x = self.dcl2(x, output_size=[-1, self.required_channels, self.out_size_required])
        x = self.activation2(x)
        return x


class Self_Attn(nn.Module):
    """ Self attention Layer"""

    def __init__(self, in_dim):
        super(Self_Attn, self).__init__()
        self.chanel_in = in_dim

        self.query_conv = nn.Conv2d(in_channels=in_dim, out_channels=in_dim // 8, kernel_size=1)
        self.key_conv = nn.Conv2d(in_channels=in_dim, out_channels=in_dim // 8, kernel_size=1)
        self.value_conv = nn.Conv2d(in_channels=in_dim, out_channels=in_dim, kernel_size=1)
        self.gamma = nn.Parameter(torch.zeros(1))

        self.softmax = nn.Softmax(dim=-1)  #

    def forward(self, x):
        """
            inputs :
                x : input feature maps( B X C X W X H)
            returns :
                out : self attention value + input feature
                attention: B X N X N (N is Width*Height)
        """
        m_batchsize, C, width, height = x.size()
        proj_query = self.query_conv(x).view(m_batchsize, -1, width * height).permute(0, 2, 1)  # B X CX(N)
        proj_key = self.key_conv(x).view(m_batchsize, -1, width * height)  # B X C x (*W*H)
        energy = torch.bmm(proj_query, proj_key)  # matrix multiplication
        attention = self.softmax(energy)  # BX (N) X (N)
        proj_value = self.value_conv(x).view(m_batchsize, -1, width * height)  # B X C X N

        out = torch.bmm(proj_value, attention.permute(0, 2, 1))
        out = out.view(m_batchsize, C, width, height)

        out = self.gamma * out + x
        return out, attention

class ResNetBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(ResNetBlock, self).__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out


class ResNet3D(nn.Module):
    def __init__(self, block=ResNetBlock, layers=[2, 2, 2, 2], channels=3, feature_maps=[64, 128, 256, 512],
                 zero_init_residual=False):
        super(ResNet3D, self).__init__()
        self.inplanes = feature_maps[0]
        self.channels = channels
        self.feature_maps = feature_maps
        self.resnet_blocks = nn.ModuleList()
        self.front_end = nn.Sequential(
            nn.Conv3d(channels, self.feature_maps[0], kernel_size=(5, 7, 7), stride=(1, 2, 2), padding=(2, 3, 3),
                      bias=False),
            nn.BatchNorm3d(self.feature_maps[0]),
            nn.ReLU(inplace=True),
            nn.MaxPool3d(kernel_size=(1, 3, 3), stride=(1, 2, 2), padding=(0, 1, 1))
        )
        self.resnet_blocks.append(self._make_layer(block, self.feature_maps[0], layers[0]))
        self.resnet_blocks.append(self._make_layer(block, self.feature_maps[1], layers[1], stride=2))
        self.resnet_blocks.append(self._make_layer(block, self.feature_maps[2], layers[2], stride=2))
        self.resnet_blocks.append(self._make_layer(block, self.feature_maps[3], layers[3], stride=2))
        self.resnet_blocks.append(nn.AdaptiveAvgPool2d((1, 1)))

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        # Zero-initialize the last BN in each residual branch,
        # so that the residual branch starts with zeros, and each residual block behaves like an identity.
        # This improves the model by 0.2~0.3% according to https://arxiv.org/abs/1706.02677
        if zero_init_residual:
            for m in self.modules():
                if isinstance(m, Bottleneck):
                    nn.init.constant_(m.bn3.weight, 0)
                elif isinstance(m, BasicBlock):
                    nn.init.constant_(m.bn2.weight, 0)

    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                conv1x1(self.inplanes, planes * block.expansion, stride),
                nn.BatchNorm2d(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.front_end(x)
        x = x.transpose(1, 2)
        x = x.contiguous()
        x = x.view(-1, self.feature_maps[0], x.size(3), x.size(4))
        for blk in self.resnet_blocks:
            x = blk(x)

        return x.view(x.size(0), -1)


class Deconv2D(nn.Module):
    def __init__(self, in_channels, out_channels, in_size, kernel_size, stride=1, batch_norm=True):
        super(Deconv2D, self).__init__()
        # This ensures that we have same padding no matter if we have even or odd kernels
        padding = same_padding(kernel_size, stride)
        self.dcl = nn.ConvTranspose2d(in_channels, out_channels, kernel_size, stride=stride, padding=padding // 2,
                                      bias=False)

        if batch_norm:
            self.activation = nn.Sequential(nn.BatchNorm2d(out_channels), nn.ReLU(True))
        else:
            self.activation = nn.ReLU(True)

        self.required_channels = out_channels
        self.out_size_required = tuple(x * stride for x in in_size)

    def forward(self, x, out_size=None):
        if out_size is None:
            x = self.dcl(x,
                         output_size=[-1, self.required_channels, self.out_size_required[0], self.out_size_required[1]])
        else:
            x = self.dcl(x, output_size=out_size)

        return self.activation(x)


class Deconv1D(nn.Module):
    def __init__(self, in_channels, out_channels, in_size, kernel_size, stride=1, batch_norm=True, use_relu=True):
        super(Deconv1D, self).__init__()
        # This ensures that we have same padding no matter if we have even or odd kernels
        padding = same_padding(kernel_size, stride)
        self.dcl = nn.ConvTranspose1d(in_channels, out_channels, kernel_size, stride=stride,
                                      padding=(padding - padding // 2))

        if batch_norm:
            if use_relu:
                self.activation = nn.Sequential(nn.BatchNorm1d(out_channels), nn.ReLU(True))
            else:
                self.activation = nn.Sequential(nn.BatchNorm1d(out_channels), nn.Tanh())
        else:
            if use_relu:
                self.activation = nn.ReLU(True)
            else:
                self.activation = nn.Tanh()

        self.required_channels = out_channels
        self.out_size_required = in_size * stride

    def forward(self, x, out_size=None):
        if out_size is None:
            x = self.dcl(x, output_size=[self.out_size_required])
        else:
            x = self.dcl(x, output_size=out_size)

        return self.activation(x)
