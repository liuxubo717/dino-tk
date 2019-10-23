import os
from .filtering import Filter, PatternExtractor, Tagger
import glob


def find_extensions(file_name):
    extensions = []

    for name in glob.glob(file_name + '*'):
        extensions.append(os.path.splitext(name)[1])

    return extensions


def list_files(folder, file_filter=None):
    file_list = []
    for root, dirs, files in os.walk(folder, topdown=False):
        if not dirs and files:
            for f in files:
                file_path = os.path.join(root, f)
                if file_filter is None or file_filter(file_path):
                    file_list.append(file_path)

    return file_list


def list_matching_files(directories, ext=None):
    file_filters = []
    for i in range(len(directories)):  # For every directory
        if ext is None or ext[i] is None:
            file_filters.append(Filter(is_file=True))  # Create a filter which just checks that the file exists
        else:
            file_filters.append(Filter(is_file=True, ext=ext[i]))  # Create a filter that requires extensions

    matching_files = []
    matching_dirs = []
    matching_exts = []
    for root, dirs, files in os.walk(directories[0], topdown=False):
        for name in files:
            file_name = os.path.splitext(name)[0]
            extensions = [os.path.splitext(name)[1]]
            if not file_filters[0](os.path.join(root, name)):
                continue

            sub_path = root.replace(directories[0], "")
            if sub_path != '' and sub_path[0] == '/':
                sub_path = sub_path[1:]

            # Now search for matching files in the other directories
            all_matches_found = True
            for i in range(1, len(directories)):
                # Find all the possible extensions of the file in the directory
                possible_extensions = find_extensions(os.path.join(directories[i], sub_path, file_name))
                if not possible_extensions:  # If you can't find any files
                    all_matches_found = False  # Report that not all matches were found and break
                    break

                # If we have found files with that name
                match_found = False
                for possible_extension in possible_extensions:  # Check all possible extensions to find only valid ones
                    if file_filters[i](os.path.join(directories[i], sub_path, file_name + possible_extension)):
                        match_found = True  # If we foind a suitable one then report and break
                        extensions.append(possible_extension)
                        break

                if not match_found:  # If no match was found report that not all matches were found
                    all_matches_found = False
                    break

            if not all_matches_found:
                continue

            matching_dirs.append(sub_path)
            matching_files.append(file_name)
            matching_exts.append(extensions)

    return {"files": matching_files, "dirs": matching_dirs, "exts": matching_exts}
