import { Stage3D } from './stage3d.js';
import { MeshMouthPlayer } from './mesh-mouth-player.js';
import * as THREE from 'three';

export async function createMeshMouthPreview(canvas,status,npc,rig) {
  const stage=new Stage3D(canvas,status),mouths=new MeshMouthPlayer();
  const scene={cast:[npc.id],beats:[],mode:'stage3d'};
  const timeline={fps:25,beats:[]};
  const previewNpc=rig.source.snapshot_file?{...npc,assets:{...npc.assets,rig_3d:{...npc.assets.rig_3d,file:rig.source.snapshot_file}}}:npc;
  try { await stage.load(scene,{[npc.id]:previewNpc},null,(id,file)=>`/api/npcs/${id}/files/${file}`); }
  catch(error){stage.dispose();throw error;}
  const actor=stage.actors[npc.id];
  if(!actor){stage.dispose();throw new Error('This character’s model could not be loaded');}
  actor.mover.position.x=0;actor.mover.updateMatrixWorld(true);
  const head=actor.bones.head;
  if(head){const focus=head.getWorldPosition(new THREE.Vector3());stage.camera.position.copy(focus).add(new THREE.Vector3(0,.02,.6));stage.camera.lookAt(focus);}
  let active=rig,dead=false;
  function draw(weights,time=0,lip=null,mouth=[]) {
    if(dead)return;
    mouths.prepare(actor);
    const frame=Math.floor(time*25);
    timeline.beats=[{id:'mouth-preview',kind:'line',speaker_id:npc.id,start:0,end:100000,motion:'stand',voice:{mouth}}];
    stage.frame(scene,timeline,frame);
    actor.mover.position.x=0;
    const ready=active.source && active.config.enabled && !['stale','missing'].includes(active.status);
    mouths.apply(actor,ready?{...active,status:'ready'}:active,weights,mouth[frame]||0);
    stage.renderer.render(stage.scene,stage.camera);
  }
  draw({sil:1});
  // Stage3D sizes only on load; keep this dialog's camera aspect correct on resize.
  const resize = new ResizeObserver(() => {
    if (!dead) stage.setOutputSize(Math.max(1,canvas.clientWidth),Math.max(1,canvas.clientHeight));
  });
  resize.observe(canvas);
  status.textContent=rig.source.targets.length?'Artist mouth morphs and jaw controls ready.':'Jaw-only model: mouth groups change opening, not lip or tongue shape.';
  return {draw,setRig:value=>{active=value;},dispose:()=>{dead=true;resize.disconnect();mouths.clear();stage.dispose();}};
}
