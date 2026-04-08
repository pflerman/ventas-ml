"""Generador de ícono para Ventas PaliShopping."""
from PIL import Image, ImageDraw, ImageFont


def draw_icon(size=512):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 512

    # Fondo redondeado estilo Palishopping (rosa/violeta)
    margin = int(8 * s)
    radius = int(72 * s)
    d.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=(255, 228, 235),
        outline=(195, 155, 211),
        width=int(6 * s),
    )

    cx = size // 2

    # --- Bolsa de shopping ---
    bag_w = int(260 * s)
    bag_h = int(240 * s)
    bag_x1 = cx - bag_w // 2
    bag_x2 = cx + bag_w // 2
    bag_y1 = int(200 * s)
    bag_y2 = bag_y1 + bag_h
    bag_radius = int(24 * s)

    # Manijas de la bolsa
    handle_w = int(8 * s)
    handle_color = (140, 90, 170)
    # Manija izquierda
    d.arc(
        [bag_x1 + int(40 * s), int(110 * s), bag_x1 + int(140 * s), int(230 * s)],
        start=180,
        end=360,
        fill=handle_color,
        width=handle_w,
    )
    # Manija derecha
    d.arc(
        [bag_x2 - int(140 * s), int(110 * s), bag_x2 - int(40 * s), int(230 * s)],
        start=180,
        end=360,
        fill=handle_color,
        width=handle_w,
    )

    # Cuerpo de la bolsa
    d.rounded_rectangle(
        [bag_x1, bag_y1, bag_x2, bag_y2],
        radius=bag_radius,
        fill=(236, 64, 122),
        outline=(140, 90, 170),
        width=int(5 * s),
    )

    # Brillo en la bolsa
    d.ellipse(
        [
            bag_x1 + int(30 * s),
            bag_y1 + int(20 * s),
            bag_x1 + int(80 * s),
            bag_y1 + int(50 * s),
        ],
        fill=(255, 200, 220),
    )

    # Símbolo $ en el centro de la bolsa
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf", int(150 * s)
        )
    except OSError:
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", int(150 * s)
            )
        except OSError:
            font = ImageFont.load_default()

    d.text(
        (cx, bag_y1 + bag_h // 2 + int(10 * s)),
        "$",
        fill=(255, 255, 255),
        font=font,
        anchor="mm",
    )

    return img


if __name__ == "__main__":
    import os

    out_dir = os.path.dirname(os.path.abspath(__file__))
    for sz in [512, 256, 128, 64, 48]:
        path = os.path.join(out_dir, f"ventas-ml-{sz}.png")
        draw_icon(sz).save(path)
        print(f"  -> {path}")
    # Ícono principal para .desktop
    main_path = os.path.join(out_dir, "ventas-ml-icon.png")
    draw_icon(256).save(main_path)
    print(f"  -> {main_path}")
