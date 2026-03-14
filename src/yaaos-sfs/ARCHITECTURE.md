# YAAOS Semantic File System (SFS) Architecture

The YAAOS Semantic File System is designed to be highly self-contained, lightweight, and entirely local by default.

This document clarifies what components exist, where they run, and how they interact with each other.

---

## 🏗️ High-Level Architecture

SFS consists of two main programs, both of which you run, and two main "dependencies" which are fully embedded within those programs (no distinct background services required).

### 1. The Core Programs (The "What you run" part)

1. **The Daemon (`yaaos-sfs`)**
   - **What it is:** A long-running Python background process.
   - **When to invoke:** You run it once in a terminal using `uv run yaaos-sfs` and leave it running in the background.
   - **What it does:** It performs an initial scan of your directory, then listens to local file system events (File Created, File Modified, File Deleted) using the `watchdog` library. When a file is modified, it extracts the text, breaks it into chunks, embeds those chunks into vectors, and saves them to the database.

2. **The Finder CLI (`yaaos-find`)**
   - **What it is:** A short-lived terminal command.
   - **When to invoke:** You run it whenever you want to search your codebase. (e.g., `uv run yaaos-find "Where is the file filtering logic?"`).
   - **What it does:** It takes your search query, converts it into a vector embedding, and asks the database for the most semantically similar chunks of code. It prints the results and quickly exits.

---

### 2. The Core Technologies (The "Where does it run" part)

A common point of confusion is over things like the "Database" or the "AI Model". **In YAAOS SFS, everything is embedded locally in the Python process.** There are *no* external standalone servers running on separate ports.

1. **The Database (`sqlite-vec`)**
   - **Where it runs:** It runs inside the Python process of whoever calls it. There is **no separate SQL server** (like PostgreSQL or MySQL) running in the background.
   - **How it works:** `sqlite-vec` acts exactly like standard SQLite but with vector math support. It reads and writes directly to a file stored locally on your disk (usually around `~/.yaaos/sfs.db`). 
   - Both the Daemon and the Finder CLI access this exact same file directly.

2. **The Local Embedding Model (`sentence-transformers`)**
   - **Where it runs:** It is downloaded to your local `.cache/huggingface` folder the first time you run YAAOS, and from then on it runs completely locally using your CPU (or GPU if configured).
   - **How it works:** When the Daemon (`yaaos-sfs`) starts, it loads the model into its memory to embed files. Similarly, when the Finder (`yaaos-find`) is executed, it loads the model into its memory to embed your search query. 
   - *Note: You can opt out of the local model by configuring the `openai` provider, which will send text over the network to OpenAI APIs instead.*

---

## 🔄 The Interaction Lifecycle (When everything happens)

Let's trace a practical example:

1. **Starting Up:** You run `uv run yaaos-sfs`.
   - The Python process starts.
   - It directly creates/opens the local SQLite file on your disk.
   - It loads the `sentence-transformers` embedding model into your machine's RAM.
   - It begins watching your files.

2. **Editing Code:** You edit `filter.py` and hit "Save".
   - The OS triggers a file-write event.
   - The `watchdog` inside the Daemon notices the change and pauses for 1.5 seconds (debouncing, in case you save multiple times in a row).
   - The Daemon reads `filter.py`, chunks the text.
   - The Daemon runs the text chunks through the Local Model in RAM to get vectors.
   - The Daemon saves those vectors into the SQLite DB file.

3. **Searching:** In a separate terminal, you run `uv run yaaos-find "How does the filter work?"`
   - A *new* Python process spawns.
   - It loads the Local Model into its RAM, and embeds your question into a vector.
   - It opens the *exact same* SQLite DB file the Daemon is actively writing to.
   - It runs a fast nearest-neighbor SQL query to find the chunks that closely match your question vector.
   - It prints the results and the process shuts down, freeing up its RAM.
