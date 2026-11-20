import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const HEIGHT = 1.7;
const START = [[-1.35, 0], [1.35, 0]];

export function blockingAt(scene, timeline, frame) {
  const positions = Object.fromEntries(scene.cast.map((id, index) => [id, [...START[index]]]));
  let current = timeline.beats.at(-1);
  for (const beat of timeline.beats) {
    if (frame < beat.end) { current = beat; break; }
    if (beat.motion === 'walk' && beat.walk_to && positions[beat.speaker_id])
      positions[beat.speaker_id] = [...beat.walk_to];
  }
  const actions = Object.fromEntries(scene.cast.map(id => [id, 'Idle']));
  if (current?.speaker_id && positions[current.speaker_id]) {
    if (current.motion === 'walk' && current.walk_to) {
      const start = positions[current.speaker_id];
      const part = Math.min(1, Math.max(0, (frame - current.start) / Math.max(1, current.frames * 0.8)));
      const eased = part * part * (3 - 2 * part);
      positions[current.speaker_id] = start.map((v, i) => v + (current.walk_to[i] - v) * eased);
      if (part < 1) actions[current.speaker_id] = 'Walk';
    } else if (current.motion === 'wave') actions[current.speaker_id] = 'Wave';
  }
  return { beat: current, positions, actions };
}

export function placeStageModel(model, asset) {
  const extras = asset?.extras;
  const recipe = extras?.body_design;
  // Production bodies already use metres and an origin between the feet.
  // Preserve their chosen height and the shared scale of appended garments.
  if (extras?.export_tier === 'game' && extras?.minimum_age === 18 && recipe?.version === 10
      && ['forge-anatomical-male-v7', 'forge-anatomical-female-v7'].includes(recipe.base)) return;
  const box = new THREE.Box3().setFromObject(model);
  const dimensions = box.getSize(new THREE.Vector3());
  const scale = HEIGHT / Math.max(0.01, dimensions.y);
  model.scale.setScalar(scale);
  const sized = new THREE.Box3().setFromObject(model);
  model.position.y -= sized.min.y;
  const center = sized.getCenter(new THREE.Vector3());
  model.position.x -= center.x;
  model.position.z -= center.z;
}

export class Stage3D {
  constructor(canvas, status) {
    this.canvas = canvas;
    this.status = status;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#17242c');
    this.camera = new THREE.PerspectiveCamera(34, 16 / 9, 0.1, 100);
    this.camera.position.set(0, 1.65, 7.2);
    this.camera.lookAt(0, 1.0, 0);
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.5;
    this.scene.add(new THREE.HemisphereLight('#ffffff', '#819096', 2.0));
    const key = new THREE.DirectionalLight('#fff1db', 2.4);
    key.position.set(-3, 7, 6);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight('#e6f0ff', 3.2);
    fill.position.set(4, 5, 7);
    this.scene.add(fill);
    const shadowCanvas = document.createElement('canvas');
    shadowCanvas.width = shadowCanvas.height = 128;
    const context = shadowCanvas.getContext('2d');
    const gradient = context.createRadialGradient(64, 64, 6, 64, 64, 60);
    gradient.addColorStop(0, '#00000099');
    gradient.addColorStop(1, '#00000000');
    context.fillStyle = gradient;
    context.fillRect(0, 0, 128, 128);
    this.shadowTexture = new THREE.CanvasTexture(shadowCanvas);
    this.actors = {};
    this.backgroundTexture = null;
    this.disposed = false;
    this.resize();
  }

  resize() {
    const width = Math.max(320, this.canvas.clientWidth || 640);
    const height = Math.max(180, this.canvas.clientHeight || 360);
    this.setOutputSize(width, height);
  }

  setOutputSize(width, height) {
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
  }

  async load(scene, npcs, backgroundUrl, modelUrl) {
    const loader = new GLTFLoader();
    let available = 0;
    for (const id of scene.cast) {
      const meta = npcs[id]?.assets?.rig_3d;
      if (!meta?.file) continue;
      this.status.textContent = `Loading ${npcs[id].profile?.first_name || 'model'}…`;
      try {
        const gltf = await loader.loadAsync(modelUrl(id, meta.file, meta.created));
        if (this.disposed) return;
        const mover = new THREE.Group();
        const model = gltf.scene;
        placeStageModel(model, gltf.asset);
        mover.add(model);
        const shadow = new THREE.Mesh(new THREE.PlaneGeometry(1.05, 0.55),
          new THREE.MeshBasicMaterial({ map: this.shadowTexture, transparent: true, depthWrite: false }));
        shadow.rotation.x = -Math.PI / 2;
        shadow.position.y = 0.015;
        mover.add(shadow);
        this.scene.add(mover);
        const mixer = new THREE.AnimationMixer(model);
        const clips = Object.fromEntries(gltf.animations.map(clip => [clip.name.toLowerCase(), clip]));
        const bones = {};
        model.traverse(item => {
          if (item.isBone) bones[item.name.toLowerCase()] = item;
          if (item.isMesh) item.frustumCulled = false;
        });
        this.actors[id] = { mover, mixer, clips, bones, clip: null, action: null };
        available++;
      } catch (error) {
        this.status.textContent = `Could not load ${npcs[id]?.profile?.first_name || id}: ${error.message}`;
      }
    }
    if (backgroundUrl) {
      try {
        const texture = await new THREE.TextureLoader().loadAsync(backgroundUrl);
        if (!this.disposed) {
          texture.colorSpace = THREE.SRGBColorSpace;
          this.scene.background = texture;
          this.backgroundTexture = texture;
        } else texture.dispose();
      } catch {
        this.status.textContent = 'Could not load the generated background.';
      }
    }
    if (!this.disposed) {
      this.status.textContent = available
        ? `${available} rigged model${available === 1 ? '' : 's'} ready. Play the scene to watch the blocking.`
        : 'No rigged 3D models are ready. Rig the chosen NPCs in their Models cards.';
      this.resize();
    }
  }

  frame(scene, timeline, frame) {
    if (this.disposed) return;
    const state = blockingAt(scene, timeline, frame);
    for (const id of scene.cast) {
      const actor = this.actors[id];
      if (!actor) continue;
      const [x, z] = state.positions[id];
      actor.mover.position.set(x, 0, z);
      const wanted = state.actions[id].toLowerCase();
      const clip = actor.clips[wanted] || actor.clips.idle || Object.values(actor.clips)[0];
      if (clip) {
        if (actor.clip !== clip) {
          actor.mixer.stopAllAction();
          actor.action = actor.mixer.clipAction(clip);
          actor.action.play();
          actor.clip = clip;
        }
        actor.action.time = ((frame / timeline.fps) % clip.duration + clip.duration) % clip.duration;
        actor.mixer.update(0);
      }
      const beat = state.beat;
      if (beat?.kind === 'line' && beat.speaker_id === id) {
        const mouth = beat.voice?.mouth || [];
        const amount = mouth[frame - beat.start] || 0;
        const jaw = actor.bones.jaw || actor.bones.jaw_01;
        if (jaw) jaw.rotation.x += amount * 0.28;
        else if (actor.bones.head) actor.bones.head.rotation.x += amount * 0.045;
      }
    }
    this.renderer.render(this.scene, this.camera);
  }

  dispose() {
    this.disposed = true;
    this.backgroundTexture?.dispose();
    this.shadowTexture?.dispose();
    for (const actor of Object.values(this.actors)) {
      actor.mixer.stopAllAction();
      actor.mover.traverse(item => {
        item.geometry?.dispose();
        if (Array.isArray(item.material)) item.material.forEach(mat => mat.dispose());
        else item.material?.dispose();
      });
    }
    this.renderer.dispose();
  }
}
