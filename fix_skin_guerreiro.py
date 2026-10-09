from pathlib import Path
import shutil

src = Path(r"C:\Users\Adrian\OneDrive\YURI\RIFTWALKER_0.11.0_INSANITY_PASS\assets\sprites\player\guerreiro\masculino.png")
dst = Path(r"C:\Users\Adrian\OneDrive\YURI\RIFTWALKER_0.11.0_INSANITY_PASS\assets\sprites\player\guerreiro\feminino.png")

if not src.exists():
    raise FileNotFoundError(f"Arquivo original não encontrado: {src}")

shutil.copyfile(src, dst)
print(f"Skin corrigida: {src.name} -> {dst.name}")
print(f"Novo tamanho: {dst.stat().st_size} bytes")
