"""Native Forge Drama studio, sharing normal GPU/residency and frozen bindings."""
from pathlib import Path
import sys
import threading
from . import engines, binding


def shared(cfg):
    lab=engines.lab_root(cfg)
    if not lab or not (lab/'drama3_production.py').is_file():
        raise ValueError('Update the shared Voice Lab Drama studio installation first.')
    if str(lab) not in sys.path:sys.path.insert(0,str(lab))
    import drama3_production
    return drama3_production


def create(app, guard):
    common=shared(app.cfg)
    def resolve(source_id):
        version=app.voice_library.get_version(source_id)
        origin=version['provenance'].get('source_npc')
        if origin:guard(origin)
        if not version['approved']:raise ValueError('Approve this voice library version before assigning it to a cast.')
        app.voice_library.read_audio(source_id)  # validates immutable audio hash
        return {'path':str(app.voice_library._contained(version['reference']['file'])),
                'text':version['reference'].get('text',''),'source_id':source_id,
                'reference_id':source_id,'allowed_use':version['provenance'],'source_npc':origin}
    def sources():
        rows=[]
        for voice in app.voice_library.list():
            for version in voice['versions']:
                if not version['approved'] or not version['reference'].get('text'):continue
                try:resolve(version['id'])
                except Exception as exc:
                    if isinstance(exc,(KeyError,ValueError,FileNotFoundError)) or getattr(exc,'status',None) in (403,404):continue
                    raise
                rows.append({'id':version['id'],'label':voice['name']+' / v'+str(version['number'])})
        return rows
    def runner(body,refs,directory,emit,cancel):
        finished=threading.Event();errors=[]
        def reader():
            path=directory/'take.pcm';offset=0
            try:
                while True:
                    if path.exists():
                        with path.open('rb') as stream:
                            stream.seek(offset)
                            available=max(0,path.stat().st_size-offset)
                            remaining=available-available%2
                            while remaining:
                                pcm=stream.read(min(176400,remaining))
                                if not pcm:break
                                emit(pcm,44100);offset+=len(pcm);remaining-=len(pcm)
                    if finished.is_set():
                        if not path.exists() or offset==path.stat().st_size:break
                        if path.stat().st_size%2:raise RuntimeError('Drama preview ended with an incomplete PCM sample.')
                    finished.wait(.05)
            except Exception as exc:errors.append(exc);cancel.set()
        reader_thread=threading.Thread(target=reader,daemon=True);reader_thread.start()
        request={'cmd':'clone' if refs else 'design','text':body['text'],'instruct':body.get('direction',''),
                 'language':body['language'],'seed':body['settings']['seed'],'params':body['settings'],
                 '_studio':True,'_studio_references':refs,'_studio_description':body.get('description','')}
        request['_runtime_binding']=body['_runtime_binding']
        failure=None
        try:
            result=app.jobs.voice.run('drama3',request,directory/'take.wav',cancel)
        except Exception as exc:failure=exc
        finally:
            finished.set();reader_thread.join(10)
            app.jobs.voice.release()
        if errors:raise errors[0]
        if failure:raise failure
        if reader_thread.is_alive():raise RuntimeError('Drama preview reader did not stop.')
        return result['model_identity']
    def prepare(body,refs):return binding.capture(app.cfg,'drama3',['clone' if refs else 'design'])
    def authorize(row):
        for ref in row['references']:
            if ref.get('source_npc'):guard(ref['source_npc'])
    return common.Studio(Path(app.lib.root)/'.voice-drama3',sources,resolve,runner,prepare=prepare,authorize=authorize)
