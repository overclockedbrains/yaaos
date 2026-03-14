import os
import argparse
import time
from pathlib import Path

# Try to use rich if installed globally, otherwise fallback to standard text
try:
    from rich.console import Console
    from rich.table import Table
    RICH_ENABLED = True
except ImportError:
    RICH_ENABLED = False

# Hardcode the SFS default configurations so this runs entirely standalone!
IGNORED_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", "node_modules", "vendor",
    "venv", ".venv", "env", ".env", "build", "dist", ".idea", ".vscode",
    "coverage", ".pytest_cache", ".ruff_cache", "target", "out"
}
SUPPORTED_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".conf", ".sh", ".bash",
    ".h", ".c", ".cpp", ".hpp", ".java", ".go", ".rs", ".sql", ".pdf",
    ".cfg", ".xml", ".csv"
}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

def format_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0

def should_index_file(filename, size):
    """Standalone replica of the 4-layer file filter."""
    if filename.startswith('.'):
        return False
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTS:
        return False
    if size > MAX_FILE_SIZE_BYTES:
        return False
    return True

def get_dir_size_fast(path):
    """Fast scan for ignored windows sizes."""
    total_size = 0
    count = 0
    stack = [str(path)]
    while stack:
        current = stack.pop()
        try:
            for entry in os.scandir(current):
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    try:
                        total_size += entry.stat(follow_symlinks=False).st_size
                        count += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return total_size, count

def main():
    parser = argparse.ArgumentParser(description="Standalone Windows YAAOS-SFS Stats Calculator.")
    parser.add_argument("directory", help="The directory to analyze", type=str, nargs="?", default=".")
    args = parser.parse_args()

    watch_dir = Path(args.directory).resolve()
    if not watch_dir.is_dir():
        print(f"Error: {watch_dir} is not a valid directory.")
        return

    print(f"Analyzing directory (Native Windows Mode): {watch_dir}")
    print(f"Using max file size: 5 MB")
    print("Scanning... (This will be much faster natively)\n")
    
    start_time = time.time()

    total_size = 0
    ignored_size = 0
    indexed_size = 0
    
    total_files = 0
    ignored_files = 0
    indexed_files = 0

    indexed_extensions = {}
    
    for root, dirs, filenames in os.walk(watch_dir):
        root_path = Path(root)
        allowed_dirs = []
        
        for d in dirs:
            if d.startswith('.') or d in IGNORED_DIRS:
                # Instantly skip crawling it for the indexer, but sum the size for the report
                dir_path = root_path / d
                s, c = get_dir_size_fast(dir_path)
                ignored_size += s
                ignored_files += c
                total_size += s
                total_files += c
            else:
                allowed_dirs.append(d)
        
        # Override so os.walk ignores bad dirs
        dirs[:] = allowed_dirs
        
        for f in filenames:
            file_path = root_path / f
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
                
            total_size += size
            total_files += 1
            
            if should_index_file(f, size):
                indexed_size += size
                indexed_files += 1
                
                ext = file_path.suffix.lower() or "no_extension"
                indexed_extensions[ext] = indexed_extensions.get(ext, 0) + size
            else:
                ignored_size += size
                ignored_files += 1

    elapsed = time.time() - start_time
    
    p_ignored = (ignored_size / total_size * 100) if total_size else 0
    p_indexed = (indexed_size / total_size * 100) if total_size else 0
    
    if RICH_ENABLED:
        console = Console()
        table = Table(title=f"Native Windows SFS Stats (Scanned in {elapsed:.2f}s)")
        table.add_column("Metric", style="cyan", justify="left")
        table.add_column("Size", style="magenta", justify="right")
        table.add_column("File Count", style="green", justify="right")
        table.add_column("Percentage", style="yellow", justify="right")
        
        table.add_row("Total Discovered Data", format_size(total_size), str(total_files), "100.0%")
        table.add_row("Ignored Data (Pruned)", format_size(ignored_size), str(ignored_files), f"{p_ignored:.1f}%")
        table.add_row("Indexed Data", format_size(indexed_size), str(indexed_files), f"{p_indexed:.1f}%")
        console.print(table)
    else:
        print(f"--- Native Windows SFS Stats (Scanned in {elapsed:.2f}s) ---")
        print(f"Total Discovered Data: {format_size(total_size):>12} | {total_files:>8} files | 100.0%")
        print(f"Ignored Data (Pruned): {format_size(ignored_size):>12} | {ignored_files:>8} files | {p_ignored:.1f}%")
        print(f"Indexed Data         : {format_size(indexed_size):>12} | {indexed_files:>8} files | {p_indexed:.1f}%")
        print("-" * 65)

    if indexed_size > 0:
        top_exts = sorted(indexed_extensions.items(), key=lambda item: item[1], reverse=True)[:5]
        print("\nTop 5 Indexed File Types (by size):")
        for ext, size in top_exts:
            print(f"  * {ext}: {format_size(size)}")

        est_db_size = indexed_size * 7
        print("\nEstimations for this folder:")
        print(f"-> Only {format_size(indexed_size)} will be chunked and passed to the ML models.")
        print(f"-> The resulting sqlite-vec database file will grow to roughly {format_size(est_db_size)}.")
    else:
        print("\nNo indexable files found.")

if __name__ == "__main__":
    main()
