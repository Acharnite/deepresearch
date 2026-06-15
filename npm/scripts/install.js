"use strict";

const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

const INSTALL_DIR = path.join(os.homedir(), ".deepresearch");
const VENV_DIR = path.join(INSTALL_DIR, "venv");

function checkPython() {
  const candidates =
    process.platform === "win32"
      ? ["python", "python3"]
      : ["python3", "python"];

  for (const cmd of candidates) {
    try {
      const result = execFileSync(cmd, ["--version"], {
        encoding: "utf8",
        timeout: 5000,
      });
      const match = result.match(/Python (\d+)\.(\d+)/);
      if (match) {
        const major = parseInt(match[1]);
        const minor = parseInt(match[2]);
        if (major >= 3 && minor >= 11) return cmd;
      }
    } catch {}
  }
  return null;
}

// Only run during npm install (not when used as CLI)
if (process.env.npm_lifecycle_event === "postinstall") {
  const python = checkPython();
  if (!python) {
    console.error(
      "\n❌ Python 3.11+ is required but not found.\n" +
        "   Install Python: https://www.python.org/downloads/\n" +
        "   Or use: brew install python3 (macOS)\n" +
        "   Or use: sudo apt install python3 (Ubuntu/Debian)\n"
    );
    process.exit(1);
  }

  console.log(`🐍 Found ${python}`);

  // Create venv if not exists
  if (!fs.existsSync(path.join(VENV_DIR, "bin", "deepresearch"))) {
    console.log("📦 Setting up virtual environment...");
    execFileSync(python, ["-m", "venv", VENV_DIR], { stdio: "inherit" });

    const pip =
      process.platform === "win32"
        ? path.join(VENV_DIR, "Scripts", "pip")
        : path.join(VENV_DIR, "bin", "pip");

    execFileSync(pip, ["install", "deepresearch"], { stdio: "inherit" });
    console.log("✅ DeepeResearch ready!\n");
  }
}
