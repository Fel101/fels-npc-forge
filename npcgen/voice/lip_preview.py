"""Retained timing-reference video, with the exact source WAV and model cues."""
import math
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

from . import alignment

SHAPES = {'sil': (32, 1), 'PP': (25, 2), 'FF': (28, 6), 'TH': (30, 10), 'DD': (30, 11), 'kk': (33, 14),
          'CH': (22, 14), 'SS': (31, 5), 'nn': (30, 12), 'RR': (24, 10), 'aa': (32, 26), 'E': (39, 12),
          'ih': (37, 7), 'oh': (21, 22), 'ou': (14, 15)}


def render(audio_path, lip_sync, output):
    alignment.validate(lip_sync, lip_sync['seconds'])
    alignment.validate_visemes(lip_sync['visemes'], lip_sync['seconds'])
    track, output = lip_sync['visemes'], Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fps = track['fps']; frames = math.ceil(lip_sync['seconds'] * fps)
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg: raise ValueError('FFmpeg is required for the timing-reference video')
    font_path = Path('C:/Windows/Fonts/segoeui.ttf')
    font = ImageFont.truetype(str(font_path), 18) if font_path.is_file() else ImageFont.load_default()
    with output.with_suffix('.log').open('wb') as log:
        process = subprocess.Popen([ffmpeg, '-n', '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', '640x400', '-r', str(fps), '-i', 'pipe:0', '-i', str(audio_path), '-map', '0:v', '-map', '1:a',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(output)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        try:
            for index in range(frames):
                t = index / fps
                weights = track['frames'][index]['weights']
                width = sum(SHAPES[key][0] * value for key, value in weights.items()) * 1.5
                height = max(1., sum(SHAPES[key][1] * value for key, value in weights.items()) * 1.5)
                image = Image.new('RGB', (640, 400), '#18222a'); draw = ImageDraw.Draw(image)
                draw.text((18, 12), 'Model timing reference — character rig integration pending', fill='#d6c9b6', font=font)
                draw.ellipse((210, 52, 430, 326), outline='#d0b999', width=3)
                for x in (275, 365): draw.ellipse((x - 5, 145, x + 5, 155), fill='#d0b999')
                draw.line([(320, 170), (308, 205), (326, 207)], fill='#d0b999', width=3)
                draw.ellipse((320 - width, 250 - height, 320 + width, 250 + height), fill='#06090c', outline='#ddadad', width=2)
                if height > 5 and any(weights.get(key, 0) > .1 for key in ('FF', 'TH', 'E', 'ih', 'SS')):
                    draw.rectangle((320 - width * .6, 250 - height + 2, 320 + width * .6, 250 - height + 6), fill='#e8e1d7')
                word = next((w['word'] for w in lip_sync['words'] if w['start'] <= t < w['end']), '')
                phone = next((p['phone'] for p in lip_sync['phonemes'] if p['start'] <= t < p['end']), '')
                shape = max(weights, key=weights.get)
                draw.text((25, 355), f'{t:.2f}s · {word} · {phone} · {shape}', fill='#e8e1d7', font=font)
                process.stdin.write(image.tobytes())
            process.stdin.close()
            if process.wait(timeout=30): raise ValueError('Timing-reference video encoding failed; see its log')
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=10)
    return {'file': str(output), 'fps': fps, 'frames': frames, 'seconds': frames / fps,
            'source_audio_sha256': lip_sync['audio_sha256'], 'role': 'timing_reference_diagram', 'character_rig': False}
