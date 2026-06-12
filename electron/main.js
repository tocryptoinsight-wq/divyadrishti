const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

let mainWindow = null;
let backendProcess = null;
let activePort = 8000;
const isDev = process.argv.includes('--dev');
let _restartAttempts = 0;
const MAX_RESTART_ATTEMPTS = 10;
let _restartBaseDelay = 2000;

function freePort(port) {
  try {
    const result = execSync(
      `netstat -ano | findstr "LISTENING" | findstr ":${port} "`,
      { encoding: 'utf8', timeout: 3000 }
    );
    const lines = result.trim().split('\n');
    for (const line of lines) {
      const parts = line.trim().split(/\s+/);
      const pid = parts[parts.length - 1];
      if (pid && pid !== '0') {
        try { execSync(`taskkill /F /PID ${pid}`, { timeout: 2000 }); } catch (_) {}
      }
    }
  } catch (_) {}
}

function getConfig() {
  const configPath = app.isPackaged
    ? path.join(process.resourcesPath, 'backend', 'config.json')
    : path.join(__dirname, 'config.json');
  try {
    return JSON.parse(fs.readFileSync(configPath, 'utf-8'));
  } catch (e) {
    return { port: 8000 };
  }
}
const configPort = getConfig().port;

function getBackendPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', 'DDScanner.exe');
  }
  return path.join(__dirname, '..', 'build', 'backend', 'DDScanner.exe');
}

function probePorts(ports, pathSuffix) {
  return new Promise((resolve) => {
    let idx = 0;
    const tryNext = () => {
      if (idx >= ports.length) { resolve(null); return; }
      const port = ports[idx++];
      const req = http.get(`http://127.0.0.1:${port}${pathSuffix}`, (res) => {
        res.resume();
        resolve(port);
      });
      req.on('error', () => setTimeout(tryNext, 300));
      req.setTimeout(1500, () => { req.destroy(); setTimeout(tryNext, 100); });
    };
    tryNext();
  });
}

function waitForBackend() {
  return new Promise((resolve, reject) => {
    let retries = 60;
    const poll = () => {
      probePorts([configPort], '/api/health').then((port) => {
        if (port) {
          activePort = port;
          console.log('[electron] Backend found on port', port);
          resolve(port);
        } else if (--retries <= 0) {
          reject(new Error('Backend did not start in time'));
        } else {
          setTimeout(poll, 1000);
        }
      });
    };
    poll();
  });
}

async function startBackend() {
  // Patient check: try for 5s in case backend is briefly busy
  for (let i = 0; i < 5; i++) {
    const found = await probePorts([configPort], '/api/health');
    if (found) {
      activePort = configPort;
      console.log('[electron] Backend already running on port', configPort);
      return;
    }
    if (i < 4) await new Promise(r => setTimeout(r, 1000));
  }
  const backendPath = getBackendPath();
  if (!backendPath || !fs.existsSync(backendPath)) {
    throw new Error('Backend executable not found');
  }
  freePort(configPort);
  backendProcess = spawn(backendPath, [String(configPort)], {
    cwd: path.join(__dirname, '..'),
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
  });
  backendProcess.stdout.on('data', (d) => console.log('[backend]', d.toString().trim()));
  backendProcess.stderr.on('data', (d) => console.log('[backend]', d.toString().trim()));
  backendProcess.on('exit', (code) => {
    console.log('[backend] exited with code:', code);
    if (_restartAttempts >= MAX_RESTART_ATTEMPTS) {
      console.log('[electron] Max restart attempts reached, giving up');
      return;
    }
    _restartAttempts++;
    const delay = Math.min(_restartBaseDelay * Math.pow(1.5, _restartAttempts - 1), 30000);
    console.log('[electron] Restart attempt', _restartAttempts, 'in', delay, 'ms');
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        _showSplash('Reconnecting... (' + _restartAttempts + '/' + MAX_RESTART_ATTEMPTS + ')');
        _startBackendAndConnect();
      }
    }, delay);
  });
}

function _showSplash(msg) {
  const page = '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>DivyaDrishti</title><style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#1a1a2e;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:\'Segoe UI\',sans-serif;color:#e0e0e0;}h1{font-size:52px;margin-bottom:10px;}h2{font-size:26px;font-weight:300;margin-bottom:20px;color:#9ca3af;letter-spacing:1px;}#s{color:#6b7280;font-size:14px;}.spinner{width:40px;height:40px;border:4px solid #2a2a4a;border-top-color:#6366f1;border-radius:50%;animation:spin 0.9s linear infinite;margin:24px auto;}@keyframes spin{to{transform:rotate(360deg);}}</style></head><body><h1>&#128302;</h1><h2>DivyaDrishti 3.0</h2><div class="spinner"></div><div id="s">' + (msg || 'Starting backend...') + '</div></body></html>';
  mainWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(page));
}

async function _startBackendAndConnect() {
  try {
    await startBackend();
    const port = await waitForBackend();
    _restartAttempts = 0;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL('http://127.0.0.1:' + port);
    }
  } catch (e) {
    console.error('[electron] Failed to connect backend:', e);
    if (_restartAttempts >= MAX_RESTART_ATTEMPTS) {
      _showSplash('Failed: ' + e.message);
    }
  }
}

ipcMain.handle('start-backend', async () => {
  try {
    await _startBackendAndConnect();
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend-ready', activePort);
    }
  } catch (e) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend-error', e.message);
    }
  }
});

ipcMain.handle('restart-backend', async () => {
  if (!getBackendPath()) {
    return; // dev mode — HTTP restart already triggered by frontend
  }
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
  _restartAttempts = 0;
  try {
    await _startBackendAndConnect();
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend-ready', activePort);
    }
  } catch (e) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend-error', e.message);
    }
  }
});

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    show: false,
    title: 'DivyaDrishti',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      backgroundThrottling: false,
    },
  });
  mainWindow.setMenuBarVisibility(false);

  if (isDev) {
    mainWindow.loadURL('http://localhost:' + configPort);
    mainWindow.show();
    return;
  }

  const splash = `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>DivyaDrishti</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#1a1a2e;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:'Segoe UI',sans-serif;color:#e0e0e0;}
h1{font-size:52px;margin-bottom:10px;}
h2{font-size:26px;font-weight:300;margin-bottom:20px;color:#9ca3af;letter-spacing:1px;}
#status{margin-top:8px;font-size:14px;color:#6b7280;min-height:20px;}
#status.error{color:#ef4444;}
.spinner{width:40px;height:40px;border:4px solid #2a2a4a;border-top-color:#6366f1;border-radius:50%;animation:spin 0.9s linear infinite;margin:24px auto;}
@keyframes spin{to{transform:rotate(360deg);}}
.bar-track{width:280px;height:5px;background:#2a2a4a;border-radius:3px;overflow:hidden;margin-top:8px;}
.bar-fill{height:100%;width:0%;background:linear-gradient(90deg,#6366f1,#22c55e);border-radius:3px;transition:width 0.4s ease;}
</style>
</head>
<body>
<h1>&#128302;</h1>
<h2>DivyaDrishti 3.0</h2>
<div id="status">Starting backend...</div>
<div class="spinner"></div>
<div class="bar-track"><div class="bar-fill" id="barFill"></div></div>
<script>
(function() {
  var pct = 0;
  var prog = setInterval(function() {
    pct = Math.min(pct + 3, 85);
    document.getElementById('barFill').style.width = pct + '%';
  }, 300);
  window.electron.startBackend();
  window.electron.onBackendReady(function(port) {
    clearInterval(prog);
    document.getElementById('barFill').style.width = '100%';
    document.getElementById('status').textContent = 'Connected on port ' + port;
  });
  window.electron.onBackendError(function(msg) {
    clearInterval(prog);
    var s = document.getElementById('status');
    s.textContent = 'Error: ' + msg;
    s.className = 'error';
    document.querySelector('.spinner').style.display = 'none';
  });
})();
</script>
</body>
</html>`;

  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(splash)}`);
  mainWindow.show();
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (backendProcess) { backendProcess.kill(); backendProcess = null; }
  app.quit();
});

app.on('before-quit', () => {
  if (backendProcess) { backendProcess.kill(); backendProcess = null; }
});
