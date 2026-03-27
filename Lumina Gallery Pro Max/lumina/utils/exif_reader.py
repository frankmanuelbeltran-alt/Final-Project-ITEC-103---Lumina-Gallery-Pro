from PIL import Image, ExifTags
from lumina.utils.logging_utils import logger


class ExifReader:
    @staticmethod
    def read_exif(image_path):
        try:
            with Image.open(image_path) as img:
                exif = img._getexif()
                if not exif:
                    return {}

                data = {}
                for tag_id, value in exif.items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    data[tag] = value

                return ExifReader._format_exif(data)
        except (IOError, OSError) as e:
            logger.debug(f"Could not read EXIF from {image_path}: {e}")
            return {}
        except Exception as e:
            logger.debug(f"Unexpected error reading EXIF from {image_path}: {e}")
            return {}

    @staticmethod
    def _format_exif(data):
        formatted = {}

        if 'Make' in data:
            formatted['Camera Make'] = str(data['Make'])
        if 'Model' in data:
            formatted['Camera Model'] = str(data['Model'])

        if 'DateTimeOriginal' in data:
            formatted['Date Taken'] = str(data['DateTimeOriginal'])

        if 'ExposureTime' in data:
            exp = data['ExposureTime']
            if isinstance(exp, tuple):
                formatted['Exposure'] = f"{exp[0]}/{exp[1]}s"
            else:
                formatted['Exposure'] = f"{exp}s"

        if 'FNumber' in data:
            fnum = data['FNumber']
            if isinstance(fnum, tuple):
                formatted['Aperture'] = f"f/{fnum[0]/fnum[1]:.1f}"
            else:
                formatted['Aperture'] = f"f/{fnum}"

        if 'ISOSpeedRatings' in data:
            formatted['ISO'] = str(data['ISOSpeedRatings'])

        if 'FocalLength' in data:
            focal = data['FocalLength']
            if isinstance(focal, tuple):
                formatted['Focal Length'] = f"{focal[0]/focal[1]:.0f}mm"
            else:
                formatted['Focal Length'] = f"{focal}mm"

        if 'GPSInfo' in data:
            gps = ExifReader._get_gps_coords(data['GPSInfo'])
            if gps:
                formatted['GPS'] = gps

        return formatted

    @staticmethod
    def _get_gps_coords(gps_info):
        try:
            from PIL import ExifTags

            gps_data = {}
            for key in gps_info.keys():
                decode = ExifTags.GPSTAGS.get(key, key)
                gps_data[decode] = gps_info[key]

            if 'GPSLatitude' in gps_data and 'GPSLongitude' in gps_data:
                lat = ExifReader._convert_dms(gps_data['GPSLatitude'])
                if gps_data.get('GPSLatitudeRef') == 'S':
                    lat = -lat

                lon = ExifReader._convert_dms(gps_data['GPSLongitude'])
                if gps_data.get('GPSLongitudeRef') == 'W':
                    lon = -lon

                return f"{lat:.6f}, {lon:.6f}"
        except (KeyError, TypeError, ZeroDivisionError):
            pass
        return None

    @staticmethod
    def _convert_dms(dms):
        degrees = dms[0][0] / dms[0][1]
        minutes = dms[1][0] / dms[1][1]
        seconds = dms[2][0] / dms[2][1]
        return degrees + minutes / 60 + seconds / 3600