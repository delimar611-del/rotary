const { app, BrowserWindow, ipcMain, Menu } = require("electron");
const fs = require("fs");
const path = require("path");

/* Podaci se spremaju u Dokumenti\Evidencija streljiva\evidencija.json,
   a u podmapi "kopije" čuva se automatska dnevna sigurnosna kopija. */
const dataDir = path.join(app.getPath("documents"), "Evidencija streljiva");
const dataFile = path.join(dataDir, "evidencija.json");
const backupDir = path.join(dataDir, "kopije");
const BACKUPS_TO_KEEP = 30;

function pad(n) { return String(n).padStart(2, "0"); }

function writeDailyBackup(text) {
  const d = new Date();
  const name = "evidencija-" + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + ".json";
  const file = path.join(backupDir, name);
  fs.mkdirSync(backupDir, { recursive: true });
  fs.writeFileSync(file, text, "utf8");

  const old = fs.readdirSync(backupDir)
    .filter((f) => /^evidencija-\d{8}\.json$/.test(f))
    .sort()
    .slice(0, -BACKUPS_TO_KEEP);
  for (const f of old) {
    try { fs.unlinkSync(path.join(backupDir, f)); } catch (e) { /* nije kritično */ }
  }
}

ipcMain.handle("evidencija:load", () => {
  try {
    return fs.readFileSync(dataFile, "utf8");
  } catch (e) {
    return null; /* prva upotreba — datoteka još ne postoji */
  }
});

ipcMain.handle("evidencija:save", (event, text) => {
  fs.mkdirSync(dataDir, { recursive: true });
  const tmp = dataFile + ".tmp";
  fs.writeFileSync(tmp, text, "utf8");
  fs.renameSync(tmp, dataFile); /* atomski, da prekid ne ošteti datoteku */
  writeDailyBackup(text);
  return true;
});

ipcMain.handle("evidencija:dataPath", () => dataFile);

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    title: "Evidencija streljiva",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  Menu.setApplicationMenu(null);
  win.loadFile(path.join(__dirname, "app", "index.html"));
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
