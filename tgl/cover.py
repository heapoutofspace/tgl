"""Playlist cover art generation"""

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import io
import base64


COVER_VERSION = "v2"


def generate_cover_art(text: str | None = None, output_format: str = 'base64') -> bytes | str:
    """Generate playlist cover art with optional text overlay

    Args:
        text: Text to render on the cover (year or episode ID). If None, returns plain cover.
        output_format: 'base64' for Spotify upload, 'bytes' for raw PNG data

    Returns:
        Base64 encoded string or raw bytes depending on output_format
    """
    # Paths
    project_root = Path(__file__).parent.parent
    assets_path = project_root / "assets"

    # If no text, just return the plain cover
    if not text:
        cover_path = assets_path / "cover.png"
        if not cover_path.exists():
            raise FileNotFoundError(f"Cover not found: {cover_path}")

        with open(cover_path, 'rb') as f:
            image_data = f.read()

        if output_format == 'base64':
            return base64.b64encode(image_data).decode('ascii')
        else:
            return image_data

    template_path = assets_path / "cover_template.png"
    font_path = assets_path / "fonts" / "OpenSans-Bold.ttf"

    # Verify files exist
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    if not font_path.exists():
        raise FileNotFoundError(f"Font not found: {font_path}")

    # Load template image
    img = Image.open(template_path)
    draw = ImageDraw.Draw(img)

    # Load font - using larger size to get 80px text height
    font = ImageFont.truetype(str(font_path), 110)

    # Get image dimensions
    img_width = img.width

    # The pink banner is approximately 110px tall at the top (y=0 to y=110)
    # Center of pink bar is at y=55
    pink_bar_center_y = 55

    # Get text bounding box to center it
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Center horizontally
    text_x = (img_width - text_width) // 2

    # Center vertically in the pink bar
    # Position text so its vertical center aligns with the pink bar center
    text_y = pink_bar_center_y - text_height

    # Draw text in white
    draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255))

    # Save to BytesIO buffer
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)

    if output_format == 'base64':
        # Return base64 encoded for Spotify API
        return base64.b64encode(buffer.read()).decode('ascii')
    else:
        # Return raw bytes
        return buffer.read()


def display_cover_inline(text: str | None = None):
    """Display cover art inline in iTerm2 terminal

    Args:
        text: Text to render on the cover. If None, displays plain cover.
    """
    import sys

    # Generate cover as base64
    image_data = generate_cover_art(text, output_format='base64')

    # iTerm2 inline image escape sequence
    # Format: ESC]1337;File=inline=1:[base64-data]BEL
    sys.stdout.write(f'\n\033]1337;File=inline=1:{image_data}\a\n')
    sys.stdout.flush()
