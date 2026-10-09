from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageEnhance
import math

ROOT = Path(__file__).resolve().parent
SRC_PLAYER = ROOT / 'assets' / 'sprites' / 'player'
SRC_ENEMY = ROOT / 'assets' / 'sprites' / 'enemies'
OUT_PLAYER = ROOT / 'assets' / 'sprites_animated' / 'player'
OUT_ENEMY = ROOT / 'assets' / 'sprites_animated' / 'enemies'
CANVAS = 256

PLAYER_STATES = {
    'idle': 4,
    'walk': 6,
    'run': 6,
    'attack': 5,
    'hurt': 3,
}
ENEMY_STATES = {
    'idle': 4,
    'walk': 6,
    'run': 6,
    'attack': 5,
    'hurt': 3,
}


def trim(img: Image.Image) -> Image.Image:
    alpha = img.getchannel('A')
    bb = alpha.getbbox()
    return img.crop(bb) if bb else img


def fit(img: Image.Image, max_w=228, max_h=236) -> Image.Image:
    img = trim(img.convert('RGBA'))
    scale = min(max_w / img.width, max_h / img.height)
    size = (max(1, int(img.width*scale)), max(1, int(img.height*scale)))
    return img.resize(size, Image.Resampling.LANCZOS)


def on_canvas(img: Image.Image, y_bias=4) -> Image.Image:
    c = Image.new('RGBA', (CANVAS, CANVAS), (0,0,0,0))
    x = (CANVAS - img.width)//2
    y = CANVAS - img.height - 10 + y_bias
    c.alpha_composite(img, (x,y))
    return c


def transform_layer(layer: Image.Image, angle=0.0, sx=1.0, sy=1.0, dx=0, dy=0, tint=None):
    w,h = layer.size
    nw,nh = max(1,int(w*sx)), max(1,int(h*sy))
    frame = layer.resize((nw,nh), Image.Resampling.BICUBIC)
    if angle:
        frame = frame.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    if tint:
        r,g,b,a = tint
        overlay = Image.new('RGBA', frame.size, (r,g,b,0))
        alpha = frame.getchannel('A')
        overlay.putalpha(alpha.point(lambda v: int(v*a/255)))
        frame = Image.alpha_composite(frame, overlay)
    c = Image.new('RGBA',(CANVAS,CANVAS),(0,0,0,0))
    x=(CANVAS-frame.width)//2+int(dx)
    y=(CANVAS-frame.height)//2+int(dy)
    c.alpha_composite(frame,(x,y))
    return c


def player_frame(base: Image.Image, state: str, i: int, n: int) -> Image.Image:
    phase = (i/n)*math.tau
    if state == 'idle':
        sy=1.0 + 0.012*math.sin(phase)
        return transform_layer(base, sy=sy, dy=1.5*math.sin(phase))
    if state in ('walk','run'):
        step = math.sin(phase)
        stride = abs(step)
        running = state == 'run'
        bob = -stride * (10.0 if running else 5.0)
        lean = step * (3.0 if running else 1.5)
        sx = 1.0 + stride * (0.018 if running else 0.008)
        sy = 1.0 - stride * (0.014 if running else 0.006)
        dx = step * (2.0 if running else 0.8)
        return transform_layer(base, angle=lean, sx=sx, sy=sy, dx=dx, dy=bob)
    if state == 'attack':
        t=i/max(1,n-1)
        impulse=math.sin(t*math.pi)
        angle=-8 + 18*t
        return transform_layer(base, angle=angle, sx=1+0.07*impulse, sy=1-0.035*impulse, dx=5*impulse, dy=-3*impulse)
    if state == 'hurt':
        t=i/max(1,n-1)
        angle=7*(1-t)
        return transform_layer(base, angle=angle, sx=0.97, sy=1.035, dx=-4*(1-t), tint=(180,30,30,45))
    return base


def enemy_frame(base: Image.Image, state: str, i: int, n: int, boss=False) -> Image.Image:
    phase=(i/n)*math.tau
    if state=='idle':
        amp=0.015 if boss else 0.025
        return transform_layer(base, sx=1+amp*math.sin(phase), sy=1-amp*0.7*math.sin(phase), dy=1.5*math.sin(phase))
    if state in ('walk','run'):
        step = math.sin(phase)
        stride = abs(step)
        running = state == 'run'
        scale = 0.7 if boss else 1.0
        lean = step * (3.2 if running else 1.5) * scale
        bob = -stride * (8.0 if running else 3.5) * scale
        sx = 1.0 + stride * (0.018 if running else 0.006) * scale
        sy = 1.0 - stride * (0.012 if running else 0.005) * scale
        dx = step * (1.5 if running else 0.6) * scale
        return transform_layer(base, angle=lean, sx=sx, sy=sy, dx=dx, dy=bob)
    if state=='attack':
        t=i/max(1,n-1); impulse=math.sin(t*math.pi)
        return transform_layer(base, angle=(-8+16*t)*(0.6 if boss else 1.0), sx=1+0.11*impulse, sy=1-0.06*impulse, dx=8*impulse, dy=-3*impulse)
    if state=='hurt':
        t=i/max(1,n-1)
        return transform_layer(base, angle=8*(1-t), sx=0.96, sy=1.04, dx=-5*(1-t), tint=(200,30,30,50))
    return base


def save_sequence(src: Path, out_root: Path, states, player=True, boss=False):
    base = on_canvas(fit(Image.open(src).convert('RGBA')))
    for state,count in states.items():
        folder=out_root/state
        folder.mkdir(parents=True,exist_ok=True)
        for i in range(count):
            frame=player_frame(base,state,i,count) if player else enemy_frame(base,state,i,count,boss=boss)
            frame.save(folder/f'{i:02d}.png', optimize=True)


def main():
    for class_dir in sorted(SRC_PLAYER.iterdir()):
        if not class_dir.is_dir(): continue
        for src in sorted(class_dir.glob('*.png')):
            gender=src.stem
            out=OUT_PLAYER/class_dir.name/gender
            save_sequence(src,out,PLAYER_STATES,player=True)
            print('player',class_dir.name,gender)
    boss_ids={'guardiao_primeira_ruptura','primeiro_rasgado','matriarca_sussurrante','cronarca_fraturado','o_nome_apagado','eco_do_riftwalker'}
    for src in sorted(SRC_ENEMY.glob('*.png')):
        save_sequence(src,OUT_ENEMY/src.stem,ENEMY_STATES,player=False,boss=src.stem in boss_ids)
        print('enemy',src.stem)

if __name__=='__main__':
    main()
