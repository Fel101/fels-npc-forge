import { api, btn, h, safe, toast } from './core.js';

export function createMeshRigEditor({npcId,onSaved,onChange}) {
  let value=null,busy=false;
  const load=async()=>{value=await api('GET',`/api/npcs/${npcId()}/voice/mesh-rig`);};
  const action=safe(async()=>{
    if(busy)return;busy=true;onChange();
    try {
      const npc=await api('PUT',`/api/npcs/${npcId()}/voice/mesh-rig`,value.config);
      await onSaved(npc);await load();toast('3D mouth mapping saved; model and speech preserved');
    }finally{busy=false;onChange();}
  });
  const field=(label,input)=>h('label',{class:'stack'},h('span',{class:'lbl'},label),input);
  const choose=(label,current,options,change)=>field(label,h('select',{'aria-label':label,disabled:busy,onchange:e=>change(e.target.value)},
    options.map(([id,name])=>h('option',{value:id,selected:id===current},name))));
  function view() {
    if(!value?.source || !value.config)return h('details',{},h('summary',{},'3D mouth targets'),h('p',{class:'hint'},value?.reason));
    const c=value.config,source=value.source;
    return h('details',{},h('summary',{},'3D mouth targets'),
      h('p',{class:'hint'},`${source.targets.length} named morph targets · ${source.joints.length} skin joints. Artist mappings are bound to this model’s checksum.`),
      h('p',{class:'hint'},source.targets.length?'Map mouth groups to the corresponding morphs. Empty targets remain neutral.':'This model has no mouth morph targets. A jaw joint can follow phonemes, but cannot create distinct lip, teeth or tongue shapes.'),
      h('label',{class:'check'},h('input',{type:'checkbox',checked:c.enabled,disabled:busy,onchange:e=>{c.enabled=e.target.checked;}}),'Enable 3D mouth controls'),
      choose('Jaw joint',c.jaw_bone,[['','No jaw joint'],...source.joints.map(name=>[name,name])],name=>{c.jaw_bone=name;}),
      choose('Jaw rotation axis',c.jaw_axis,['x','y','z'].map(axis=>[axis,axis]),axis=>{c.jaw_axis=axis;}),
      field('Maximum jaw opening (degrees)',h('input',{type:'number',min:0,max:34.37,step:.5,value:(c.jaw_radians*180/Math.PI).toFixed(2),
        'aria-label':'Maximum jaw opening (degrees)',disabled:busy,oninput:e=>{c.jaw_radians=Number(e.target.value)*Math.PI/180;}})),
      h('label',{class:'check'},h('input',{type:'checkbox',checked:c.jaw_sign===-1,disabled:busy,onchange:e=>{c.jaw_sign=e.target.checked?-1:1;}}),'Reverse jaw direction'),
      h('div',{class:'form-grid'},Object.keys(c.jaw_amounts).map(group=>field(`Jaw pose for ${group}`,h('input',{type:'number',min:0,max:1,step:.05,value:c.jaw_amounts[group],
        'aria-label':`Jaw pose for ${group}`,disabled:busy,oninput:e=>{c.jaw_amounts[group]=Number(e.target.value);}})))),
      source.targets.length?h('div',{class:'form-grid'},Object.keys(c.mapping).map(group=>choose(`3D target for ${group}`,c.mapping[group],
        [['','Neutral'],...source.targets.map(name=>[name,name])],name=>{c.mapping[group]=name;})),
        choose('Amplitude mouth target',c.amplitude_target,[['','No morph target'],...source.targets.map(name=>[name,name])],name=>{c.amplitude_target=name;})):null,
      btn('Save 3D mouth targets','save',action,{cls:'small',disabled:busy}),
      h('p',{class:'hint'},'Select the jaw joint intended by the rig author. Check its axis and direction in the preview; unrelated joints will move other body parts. Native 3D scenes use these controls, with amplitude fallback when alignment is unavailable.'));
  }
  return {load,view,rig:()=>value,busy:()=>busy};
}
