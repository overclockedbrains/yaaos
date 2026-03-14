import os
import argparse
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
except ImportError:
    print("Please run this script inside the uv environment where 'rich' is installed.")
    print("Example: uv run python scripts/calc_stats.py .")
    exit(1)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
from yaaos_sfs.config import Config
from yaaos_sfs.filter import FileFilter

def format_size(size_bytes):
    """Convert bytes to a human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0

def get_dir_size_fast(path):
    """Extremely fast non-recursive scandir implementation to get size of ignored folders."""
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
    parser = argparse.ArgumentParser(description="Calculate SFS indexing statistics for a directory.")
    parser.add_argument("directory", help="The directory to analyze", type=str, nargs="?", default=".")
    args = parser.parse_args()

    watch_dir = Path(args.directory).resolve()
    if not watch_dir.is_dir():
        print(f"Error: {watch_dir} is not a valid directory.")
        return

    # Load default config but override the watch directory
    config = Config.load()
    config.watch_dir = watch_dir
    
    file_filter = FileFilter(config.watch_dir, config.supported_extensions, config.max_file_size_mb)
    
    console = Console()
    console.print(f"[bold blue]Analyzing directory:[/] {watch_dir}")
    console.print(f"[dim]Using max file size: {config.max_file_size_mb}MB[/dim]")

    if "/mnt/c/" in str(watch_dir) or "/mnt/d/" in str(watch_dir):
        console.print("[bold yellow]Warning:[/] You are scanning a Windows drive mounted in WSL (/mnt/c/).")
        console.print("         Because of the '9P protocol' latency crossing from Linux to Windows,")
        console.print("         file stats (like os.walk) are 10x-100x slower than on a native Linux filesystem (~/).")
        console.print("         Counting gigabytes of ignored files here will cause a heavy delay.")
    
    total_size = 0
    ignored_size = 0
    indexed_size = 0
    
    total_files = 0
    ignored_files = 0
    indexed_files = 0

    indexed_extensions = {}
    
    with console.status("[bold green]Scanning files (calculating full ignored size)...") as status:
        for root, dirs, filenames in os.walk(watch_dir):
            root_path = Path(root)
            
            allowed_dirs = []
            for d in dirs:
                dir_path = root_path / d
                if not file_filter.is_dir_allowed(dir_path):
                    # It's an ignored directory. Let's recursively sum its size to add to our "Ignored" stats.
                    # This uses the ultra-fast scandir method, but still requires heavy I/O on WSL /mnt/c/.
                    size_of_ignored_dir, count_ignored_files = get_dir_size_fast(dir_path)
                    ignored_size += size_of_ignored_dir
                    ignored_files += count_ignored_files
                    
                    total_size += size_of_ignored_dir
                    total_files += count_ignored_files
                else:
                    allowed_dirs.append(d)
            
            # Overwrite dirs list so os.walk only traverses allowed directories
            dirs[:] = allowed_dirs
            
            # 2. File-Level Filtering
            for f in filenames:
                file_path = root_path / f
                try:
                    size = file_path.stat().st_size
                except OSError:
                    continue
                    
                total_size += size
                total_files += 1
                
                # Use the exact same should_index check the Watchdog uses
                if file_filter.should_index(file_path):
                    indexed_size += size
                    indexed_files += 1
                    
                    ext = file_path.suffix.lower() or "no_extension"
                    indexed_extensions[ext] = indexed_extensions.get(ext, 0) + size
                else:
                    ignored_size += size
                    ignored_files += 1

    table = Table(title="YAAOS Semantic File System - 4-Layer Filter Statistics")
    table.add_column("Metric", style="cyan", justify="left")
    table.add_column("Size", style="magenta", justify="right")
    table.add_column("File Count", style="green", justify="right")
    table.add_column("Percentage", style="yellow", justify="right")
    
    p_ignored = (ignored_size / total_size * 100) if total_size else 0
    p_indexed = (indexed_size / total_size * 100) if total_size else 0
    
    table.add_row("Total Discovered Data", format_size(total_size), str(total_files), "100.0%")
    table.add_row("Ignored Data (Pruned)", format_size(ignored_size), str(ignored_files), f"{p_ignored:.1f}%")
    table.add_row("Indexed Data (Processed)", format_size(indexed_size), str(indexed_files), f"{p_indexed:.1f}%")
    
    console.print()
    console.print(table)
    
    if indexed_size > 0:
        # Sort extensions by size
        top_exts = sorted(indexed_extensions.items(), key=lambda item: item[1], reverse=True)[:5]
        console.print("\n[bold cyan]Top 5 Indexed File Types (by size):[/]")
        for ext, size in top_exts:
            console.print(f"  • {ext}: {format_size(size)}")

        # Print estimations
        est_db_size = indexed_size * 7 # Based on 384-dim floats + HNSW graph overhead per chunk
        console.print("\n[bold underline red]Estimations for this folder:[/]")
        console.print(f"🚀 Only [bold green]{format_size(indexed_size)}[/] will be chunked and passed through `sentence-transformers`.")
        console.print(f"📦 The resulting `sqlite-vec` database file will grow to approx. [bold yellow]{format_size(est_db_size)}[/].")
    else:
        console.print("\n[bold red]No files found to index![/]")

if __name__ == "__main__":
    main()
