// Keep model-viewer's optional Lottie texture loader local. NPC Forge's GLB preview supports standard glTF animations,
// but does not implement the non-standard EXT_lottie_texture extension.
export class LottieLoader {
  load(url, onLoad, onProgress, onError) {
    const error = new Error('Lottie texture animations are not supported by NPC Forge preview.');
    if (typeof onError === 'function') onError(error);
    else throw error;
  }
}

export default LottieLoader;
