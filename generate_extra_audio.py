from generate_audio_assets import *


def arrow_hit(v):
    d=.30
    wood=bandpass(noise(d),500,4500)*np.exp(-tvec(d)*13)*.32
    ping=sine(980+v*130,d)*np.exp(-tvec(d)*10)*.18
    thunk=sine(82,d)*np.exp(-tvec(d)*15)*.5
    return stereo_widen(wood+ping+thunk,.15)


def arcane_impact(v):
    d=.48
    pulse=sweep(900+v*100,180,d)*env(d,.002,.26)*.24
    shimmer=(sine(1320+v*80,d)+.5*sine(1760,d))*np.exp(-tvec(d)*7)*.18
    air=bandpass(noise(d),800,7000)*env(d,.005,.25)*.12
    return stereo_widen(pulse+shimmer+air,.35)


def lightning_impact():
    d=.42
    crack=highpass(noise(d),2500)*np.exp(-tvec(d)*10)*.34
    snap=sweep(3200,280,d)*env(d,.001,.20)*.20
    low=sine(70,d)*np.exp(-tvec(d)*13)*.55
    return stereo_widen(crack+snap+low,.32)


def void_impact():
    d=.75
    low=sweep(88,34,d,'saw')*env(d,.004,.42)*.28
    reverse=sweep(320,1300,d)*env(d,.02,.38)*.18
    air=bandpass(noise(d),100,2500)*env(d,.01,.42)*.16
    return stereo_widen(low+reverse+air,.5)


def heal_sound():
    d=.65
    x=np.zeros(int(SR*d))
    for i,f in enumerate([523.25,659.25,783.99]):
        start=int(i*.08*SR)
        s=sine(f,.48)*env(.48,.005,.40)*.18
        x[start:start+len(s)] += s[:len(x)-start]
    return stereo_widen(x,.28)


def boss_death():
    d=2.3
    hit=sine(41,d)*np.exp(-tvec(d)*3.8)*.75
    fall=sweep(320,32,d,'saw')*env(d,.01,.85)*.23
    shatter=highpass(noise(d),1800)*np.exp(-tvec(d)*2.8)*.12
    return stereo_widen(hit+fall+shatter,.55)



def footstep(v):
    d=.18
    thump=sine(72+v*5,d)*np.exp(-tvec(d)*22)*.35
    grit=bandpass(noise(d),180,2600)*np.exp(-tvec(d)*18)*(.10+.01*v)
    return stereo_widen(thump+grit,.12)


def enemy_step(kind):
    d=.24 if kind=='heavy' else .18
    base={'crawler':105,'light':82,'heavy':48}[kind]
    gain={'crawler':.22,'light':.25,'heavy':.50}[kind]
    thump=sine(base,d)*np.exp(-tvec(d)*(16 if kind=='heavy' else 22))*gain
    grit=bandpass(noise(d),120,2800)*np.exp(-tvec(d)*18)*(.10 if kind!='heavy' else .16)
    return stereo_widen(thump+grit,.18)


def enemy_attack(melee=True):
    d=.34 if melee else .42
    if melee:
        body=sweep(260,70,d,'saw')*env(d,.002,.18)*.24
        air=highpass(noise(d),1200)*np.exp(-tvec(d)*12)*.12
    else:
        body=sweep(420,1250,d)*env(d,.004,.24)*.18
        air=bandpass(noise(d),700,5200)*env(d,.003,.22)*.10
    return stereo_widen(body+air,.28)


def enemy_hurt():
    d=.28
    body=sweep(210,92,d,'saw')*env(d,.002,.20)*.25
    grit=bandpass(noise(d),350,3600)*np.exp(-tvec(d)*13)*.10
    return stereo_widen(body+grit,.20)


def enemy_roar():
    d=1.05
    low=sweep(115,54,d,'saw')*env(d,.015,.45)*.34
    mid=sweep(260,105,d,'saw')*env(d,.01,.42)*.18
    air=bandpass(noise(d),180,2500)*env(d,.015,.50)*.10
    return stereo_widen(low+mid+air,.35)

save_ogg('assets/audio/combat/arrow_hit_01.ogg', arrow_hit(0))
save_ogg('assets/audio/combat/arrow_hit_02.ogg', arrow_hit(1))
save_ogg('assets/audio/combat/arcane_impact_01.ogg', arcane_impact(0))
save_ogg('assets/audio/combat/arcane_impact_02.ogg', arcane_impact(1))
save_ogg('assets/audio/combat/lightning_impact.ogg', lightning_impact())
save_ogg('assets/audio/combat/void_impact.ogg', void_impact())
save_ogg('assets/audio/combat/heal.ogg', heal_sound())
save_ogg('assets/audio/boss/death.ogg', boss_death())

for i in range(1,5):
    save_ogg(f'assets/audio/combat/footstep_{i:02d}.ogg', footstep(i-1))
save_ogg('assets/audio/enemy/step_crawler.ogg', enemy_step('crawler'))
save_ogg('assets/audio/enemy/step_light.ogg', enemy_step('light'))
save_ogg('assets/audio/enemy/step_heavy.ogg', enemy_step('heavy'))
save_ogg('assets/audio/enemy/attack_melee.ogg', enemy_attack(True))
save_ogg('assets/audio/enemy/attack_ranged.ogg', enemy_attack(False))
save_ogg('assets/audio/enemy/hurt.ogg', enemy_hurt())
save_ogg('assets/audio/enemy/roar.ogg', enemy_roar())
print('extras generated')
