# Troubleshooting

- **A yellow banner says the page is newer than the server.** The server is a long-running process, while the web page is read
  from disk on every request, so after you update the code an already-running server is still the old version. Close its
  window and start it again (`start.bat`). A job in progress is stopped and resumes from its first unfinished step when the server starts again; letting the queue finish first saves that step's work.
- **"An older NPC Forge (1.0) is running".** The launcher opens that copy. Close its window and run `start.bat` again to use
  the updated copy.
- **"Could not start NPC Forge on …".** Close the app using that port and try again. To run a second copy, set
  `NPCGEN_LIBRARY` to a separate library and run `python server.py --port 8768`.
- **`start.bat` finds an unresponsive process.** It shows the process ID and asks before stopping it. Let any active generation
  finish before choosing to restart; the launcher then opens a fresh NPC Forge server. A responsive copy opens directly.
- **"Cannot reach ComfyUI".** Start ComfyUI first; the page recovers by itself. `python -m npcgen health` also checks the
  model files. A ComfyUI that is in the middle of heavy work (a 3D model, a big model read from disk) can stop answering for minutes while its graph carries on: a job that is
  waiting for one tries again for up to 15 minutes ("ComfyUI is busy and not answering: still waiting for its graph" on the step) before giving up, and only 30 seconds when nothing is
  listening at all. A graph whose job gave up anyway still finishes in ComfyUI; its file is in ComfyUI's `output/npcforge/` folder, and **Use my own 3D model…** (or *Import a 3D model
  made elsewhere*) can bring a model back from there.
