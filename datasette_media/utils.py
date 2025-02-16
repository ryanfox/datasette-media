from datasette.utils.asgi import Response
import imghdr
import io
from PIL import Image, ExifTags

try:
    import pyheif
except ImportError:
    pyheif = None

heic_magics = {b"ftypheic", b"ftypheix", b"ftyphevc", b"ftyphevx"}
ORIENTATION_EXIF_TAG = dict((v, k) for k, v in ExifTags.TAGS.items())["Orientation"]

# Sanity check maximum width/height for resized images
DEFAULT_MAX_WIDTH_HEIGHT = 4000


VIDEO_FRAME_TEMPLATE = """
<a href="{url}" title="{url}">
  <img src="{url}" width=200 style="max-width: unset;" loading="lazy">
</a>
"""


def image_type_for_bytes(b):
    image_type = imghdr.what(None, b)
    if image_type is not None:
        return image_type
    # Maybe it's an HEIC?
    if len(b) < 12:
        return None
    if b[4:12] in heic_magics:
        return "heic"
    return None


def should_transform(row, config, request):
    # Decides if the provided row should be transformed, based on request AND config
    # Returns None if it should not be, or a dict of resize/etc options if it should
    row_keys = row.keys()
    transform = {}
    if any(
        key in row_keys for key in ("resize_width", "resize_height", "output_format")
    ):
        transform = dict(
            width=row["resize_width"] if "resize_width" in row_keys else None,
            height=row["resize_height"] if "resize_height" in row_keys else None,
            format=row["output_format"] if "output_format" in row_keys else None,
        )
    if config.get("enable_transform"):
        max_width_height = config.get("max_width_height") or DEFAULT_MAX_WIDTH_HEIGHT
        # URL arguments over-ride columns
        if "format" in request.args:
            transform["format"] = request.args["format"]
        # If either width or height is set, ignore the ones from the DB row
        if "w" in request.args or "h" in request.args:
            transform.pop("width", None)
            transform.pop("height", None)
        for urlarg, key in {"w": "width", "h": "height"}.items():
            if urlarg in request.args and int(request.args[urlarg]) < max_width_height:
                transform[key] = int(request.args[urlarg])

    return transform or None


def transform_image(image_bytes, width=None, height=None, format=None):
    image_type = image_type_for_bytes(image_bytes)
    if image_type == "heic" and pyheif is not None:
        heic = pyheif.read_heif(image_bytes)
        image = Image.frombytes(mode=heic.mode, size=heic.size, data=heic.data)
    else:
        image = Image.open(io.BytesIO(image_bytes))
    # Does EXIF tell us to rotate it?
    try:
        exif = dict(image._getexif().items())
        if exif[ORIENTATION_EXIF_TAG] == 3:
            image = image.rotate(180, expand=True)
        elif exif[ORIENTATION_EXIF_TAG] == 6:
            image = image.rotate(270, expand=True)
        elif exif[ORIENTATION_EXIF_TAG] == 8:
            image = image.rotate(90, expand=True)
    except (AttributeError, KeyError, IndexError):
        pass

    # Resize based on width and height, if set
    image_width, image_height = image.size
    if width is not None or height is not None:
        if height is None:
            # Set h based on w
            height = int((float(image_height) / image_width) * width)
        elif width is None:
            # Set w based on h
            width = int((float(image_width) / image_height) * height)
        image = image.resize((width, height))

    return image


class ImageResponse(Response):
    def __init__(self, image, format=None):
        self.image = image
        output_image = io.BytesIO()
        if format is None:
            if image.format == "GIF":
                format = "GIF"
            elif image.mode == "RGBA":
                format = "PNG"
            else:
                format = "JPEG"
        image.save(output_image, format)
        super().__init__(
            body=output_image.getvalue(),
            content_type="image/{}".format(format or "JPEG").lower(),
        )
