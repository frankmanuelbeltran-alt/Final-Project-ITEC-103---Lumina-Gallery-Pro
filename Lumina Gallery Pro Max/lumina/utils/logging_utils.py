import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('lumina_gallery_pro_max.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('LuminaGalleryProMax')