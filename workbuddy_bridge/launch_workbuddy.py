"""GUI launcher for WorkBuddy's loopback native SDK automation entry."""
from pathlib import Path
import argparse,ctypes,json,os,socket,subprocess,urllib.request

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--exe',default=os.environ.get('WORKBUDDY_DESKTOP_EXE',''))
    parser.add_argument('--port',type=int,default=int(os.environ.get('WORKBUDDY_NATIVE_PORT','18794')))
    args=parser.parse_args()
    exe=Path(args.exe)
    if not exe.is_file() or exe.name.lower()!='workbuddy.exe':raise RuntimeError('WorkBuddy 桌面程序路径无效。')
    if not 1024<=args.port<=65535:raise RuntimeError('本机自动化端口无效。')
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/json/list',timeout=1) as r:
            pages=json.load(r)
        if any(p.get('type')=='page' and '/WorkBuddy/resources/app.asar/renderer/index.html' in p.get('url','') for p in pages):
            subprocess.Popen([str(exe)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return
    except Exception:pass
    query="Get-CimInstance Win32_Process -Filter \"Name='WorkBuddy.exe'\" | Where-Object { $_.CommandLine -notmatch '--type=|app.asar|prewarm|cli\\\\bin' } | Select-Object -ExpandProperty ProcessId"
    result=subprocess.run(['powershell','-NoProfile','-Command',query],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.stdout.strip():raise RuntimeError('WorkBuddy 当前使用普通启动模式。请先完整退出 WorkBuddy，再使用“WorkBuddy - Codex 原生对话”快捷方式打开。正在运行的任务不会被本启动器强制中断。')
    with socket.socket() as listener:listener.bind(('127.0.0.1',args.port))
    subprocess.Popen([str(exe),f'--remote-debugging-port={args.port}','--remote-debugging-address=127.0.0.1'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

if __name__=='__main__':
    try:main()
    except Exception as error:
        ctypes.windll.user32.MessageBoxW(None,str(error),'WorkBuddy 原生对话启动器',0x10)
