"""Local durable job status and cancellation; one active model investigation."""
import copy
import json
import threading
import uuid
from datetime import datetime,timezone
from pathlib import Path

class Jobs:
    def __init__(self,data,atomic_json):
        self.root=Path(data)/'ai-jobs';self.atomic=atomic_json;self.lock=threading.RLock();self.serial=threading.Lock();self.active={}
        for path in self.root.glob('*.json') if self.root.exists() else []:
            try:
                job=json.loads(path.read_text(encoding='utf-8'))
                if job['status'] in ['pending','running','cancelling']:
                    job.update(status='interrupted',progress='Server restarted; start a new investigation.');self.atomic(path,job)
            except (OSError,ValueError,KeyError):pass

    def _path(self,id):
        if not isinstance(id,str) or len(id)!=32 or any(c not in '0123456789abcdef' for c in id):raise ValueError('Invalid investigation identifier.')
        return self.root/(id+'.json')

    def get(self,id):
        with self.lock:
            path=self._path(id)
            if not path.exists():raise ValueError('Investigation not found.')
            return json.loads(path.read_text(encoding='utf-8'))

    def update(self,id,**fields):
        with self.lock:
            job=self.get(id);job.update(copy.deepcopy(fields));self.atomic(self._path(id),job)

    def start(self,run_id,question,investigation,work):
        id=uuid.uuid4().hex
        cancel=investigation.cancel
        job={'id':id,'run_id':run_id,'question':question,'status':'pending','progress':'Waiting to investigate…','created_at':datetime.now(timezone.utc).isoformat(),'evidence_manifest':investigation.manifest}
        with self.lock:
            if any(self.get(j)['status'] in ['pending','running','cancelling'] and self.get(j)['run_id']==run_id for j in self.active):raise ValueError('This run already has an active investigation.')
            self.atomic(self._path(id),job);self.active[id]=cancel
        investigation.progress=lambda message:self.update(id,progress=message,evidence_manifest=investigation.manifest)
        def worker():
            try:
                with self.serial:
                    if cancel.is_set():raise ValueError('Investigation cancelled.')
                    self.update(id,status='running',progress='Calculating original-sample evidence…')
                    answer=work()
                    if cancel.is_set():raise ValueError('Investigation cancelled.')
                    self.update(id,status='completed',progress='Engineering response saved.',answer=answer,evidence_manifest=investigation.manifest)
            except Exception as exc:
                self.update(id,status='cancelled' if cancel.is_set() else 'failed',error=str(exc),progress=str(exc),evidence_manifest=investigation.manifest)
            finally:
                with self.lock:self.active.pop(id,None)
        threading.Thread(target=worker,daemon=True).start()
        return self.get(id)

    def cancel(self,id):
        with self.lock:
            job=self.get(id)
            if id in self.active:self.active[id].set();self.update(id,status='cancelling',progress='Stopping investigation…')
            return self.get(id)
