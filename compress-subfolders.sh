#!/bin/bash

# Loop through each subdirectory in the source directory
for dir in "$1"/*/; do
    # Get the folder name without the trailing slash
    folder_name=$(basename "$dir")

    # Define the output tar.gz file path
    output_file="$1/${folder_name}.tar.gz"

    # Compress the directory into a tar.gz archive
    echo "Compressing $folder_name to $output_file..."
    tar -czf "$output_file" -C "$1" "$folder_name"

    echo "$folder_name compressed successfully."
done

echo "All folders have been compressed into individual .tar.gz archives."
