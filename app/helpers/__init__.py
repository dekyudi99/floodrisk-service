from .sld_generator import generate_raster_sld

def fc_to_dict(fc):
    """
    Mengonversi ee.FeatureCollection Google Earth Engine 
    menjadi list of dictionaries (properti fitur).
    """
    features = fc.getInfo().get('features', [])
    return [f.get('properties', {}) for f in features]