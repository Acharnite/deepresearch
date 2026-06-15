#!/usr/bin/env node
"use strict";

const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const INSTALL_DIR = path.join(require("os").homedir(), ".deepresearch");
const VENV_DIR = path.join(INSTALL_DIR, "venv");

function getBinPath() {
  const binDir = process.platform === "win32" ? "Scripts" : "bin";
  return path.join(VENV_DIR, binDir, "deepresearch");
}

function ensureInstalled() {
  if (fs.existsSync(getBinPath())) return;

  console.log("🔧 Setting up DeepeResearch (first-time install)...");

  const python =
    process.platform === "win32"
      ? "python"
      : process.env.PYTHON || "python3";

  // Create venv
  execFileSync(python, ["-m", "venv", VENV_DIR], { stdio: "inherit" });

  // Install from PyPI
  const pip =
    process.platform === "win32"
      ? path.join(VENV_DIR, "Scripts", "pip")
      : path.join(VENV_DIR, "bin", "pip");

  console.log("📦 Installing DeepeResearch from PyPI...");
  execFileSync(pip, ["install", "deepresearch"], { stdio: "inherit" });

  console.log("✅ DeepeResearch installed!\n");
}

ensureInstalled();

// Forward args to Python CLI using execFileSync (safe, no shell injection)
try {
  execFileSync(getBinPath(), process.argv.slice(2), {
    stdio: "inherit",
    cwd: process.cwd(),
  });
} catch (e) {
  process.exit(e.status || 1);
}
