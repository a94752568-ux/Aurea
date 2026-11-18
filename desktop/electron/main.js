/* AUREA Studio — proceso principal de Electron.
 *
 * Responsabilidades:
 *  1. Arrancar el servidor Python (aurea/server.py) con el venv local.
 *  2. Esperar a que /api/health responda.
 *  3. Abrir la ventana (frameless con barra de título propia) y proveer
 *     diálogos nativos (libros y muestras de voz).
 *
 * v0.6: ventana sin marco del sistema (frame: false) con controles
 * min/max/cerrar propios vía IPC, y tamaño ajustado al área de trabajo
 * de la pantalla (auto-maximiza en pantallas pequeñas).
 */
const { app, BrowserWindow, dialog, ipcMain, screen } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');

const AUREA_ROOT = path.resolve(__dirname, '..', '..'); // raíz del repo
const PORT = Number(process.env.AUREA_PORT || 8765);
const SERVER_URL = `http://127.0.0.1:${PORT}/api/health`;

let pyProc = null;
let win = null;

function pythonBin() {
  if (process.env.AUREA_PYTHON) return process.env.AUREA_PYTHON;
  const candidates = [
    path.join(AUREA_ROOT, '.venv', 'bin', 'python'),
    path.join(os.homedir(), 'aurea', '.venv', 'bin', 'python'),
    '/usr/bin/python3',
  ];
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch (_) {}
  }
  return 'python3';
}

function startServer() {
  const script = path.join(AUREA_ROOT, 'aurea', 'server.py');
  pyProc = spawn(pythonBin(), [script], {
    cwd: AUREA_ROOT,
    env: { ...process.env, AUREA_PORT: String(PORT) },
    stdio: ['ignore', 'inherit', 'inherit'],
  });
  pyProc.on('exit', (code) => {
    console.log(`[aurea] servidor python salió (${code})`);
    pyProc = null;
  });
}

function stopServer() {
  if (pyProc) {
    try { pyProc.kill(); } catch (_) {}
    pyProc = null;
  }
}

function waitForServer(retries = 60) {
  return new Promise((resolve, reject) => {
    const attempt = (n) => {
      http.get(SERVER_URL, (res) => {
        res.resume();
        if (res.statusCode === 200) return resolve();
        retry(n);
      }).on('error', () => retry(n));
    };
    const retry = (n) => {
      if (n <= 0) return reject(new Error(
        'El servidor AUREA no respondió. Revisa que exista el venv con las ' +
        'dependencias de las Fases 1-4c (kokoro, numpy, soundfile).'));
      setTimeout(() => attempt(n - 1), 500);
    };
    attempt(retries);
  });
}

function registerIpc() {
  ipcMain.handle('dialog:openBook', async () => {
    const r = await dialog.showOpenDialog(win, {
      title: 'Importar libro',
      properties: ['openFile'],
      filters: [
        { name: 'Libros', extensions: ['txt', 'md', 'epub', 'pdf'] },
        { name: 'Todos los archivos', extensions: ['*'] },
      ],
    });
    return r.canceled ? null : r.filePaths[0];
  });

  ipcMain.handle('dialog:openAudio', async () => {
    const r = await dialog.showOpenDialog(win, {
      title: 'Importar muestra de voz',
      properties: ['openFile'],
      filters: [
        { name: 'Audio', extensions: ['mp3', 'wav', 'flac', 'm4a', 'ogg', 'opus'] },
        { name: 'Todos los archivos', extensions: ['*'] },
      ],
    });
    return r.canceled ? null : r.filePaths[0];
  });

  // Controles de ventana de la barra de título propia
  ipcMain.on('window:minimize', () => { if (win) win.minimize(); });
  ipcMain.on('window:maximizeToggle', () => {
    if (!win) return;
    if (win.isMaximized()) win.unmaximize();
    else win.maximize();
  });
  ipcMain.on('window:close', () => { if (win) win.close(); });
  ipcMain.handle('window:isMaximized', () => (win ? win.isMaximized() : false));
}

function createWindow() {
  // Tamaño dentro del área de trabajo real (respeta barra de tareas):
  // nunca más grande que la pantalla.
  const wa = screen.getPrimaryDisplay().workArea;
  const width = Math.min(1440, wa.width - 24);
  const height = Math.min(920, wa.height - 24);

  win = new BrowserWindow({
    width,
    height,
    minWidth: 1024,
    minHeight: 640,
    x: wa.x + Math.floor((wa.width - width) / 2),
    y: wa.y + Math.floor((wa.height - height) / 2),
    frame: false, // barra de título propia (styles.css .titlebar)
    backgroundColor: '#070b14',
    title: 'AUREA Studio',
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // En pantallas que no llegan al tamaño de diseño, maximizar directamente.
  if (wa.width < 1500 || wa.height < 960) win.maximize();

  win.loadFile(path.join(__dirname, '..', 'index.html'));
  win.once('ready-to-show', () => win.show());

  // Avisar al renderer cuando cambia el estado maximizado (icono □/❐)
  const notifyMax = () => {
    if (win && !win.isDestroyed()) {
      win.webContents.send('window:maximizedChanged', win.isMaximized());
    }
  };
  win.on('maximize', notifyMax);
  win.on('unmaximize', notifyMax);
}

app.whenReady().then(async () => {
  registerIpc();
  startServer();
  try {
    await waitForServer();
  } catch (e) {
    dialog.showErrorBox('AUREA Studio', String(e.message || e));
  }
  createWindow();
});

app.on('before-quit', stopServer);
app.on('window-all-closed', () => {
  stopServer();
  app.quit();
});
