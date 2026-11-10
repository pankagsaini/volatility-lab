"""Standalone local dashboard server."""
import os
from pathlib import Path
import sys
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/'.dependencies'))
os.chdir(root)
if __name__=='__main__':
    from desktop import install_stop_listener
    install_stop_listener()
    from streamlit.web import cli
    sys.argv=['streamlit','run',str(root/'dashboard.py'),'--server.address=127.0.0.1',
              '--server.port=8501','--server.headless=true','--browser.gatherUsageStats=false']
    raise SystemExit(cli.main())
