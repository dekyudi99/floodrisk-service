import ee
import os
import json
from json import loads
import urllib.parse
from flask import jsonify
import logging
from dotenv import load_dotenv
from app.helpers.sld_generator import generate_raster_sld

load_dotenv()

KEY_PATH = os.path.join(os.path.dirname(__file__), '.private-key.json')
SERVICE_ACCOUNT_EMAIL = os.getenv("GEE_SERVICE_ACCOUNT")

# Inisialisasi GEE agar aman dari restart worker Flask
try:
    credentials = ee.ServiceAccountCredentials(SERVICE_ACCOUNT_EMAIL, KEY_PATH)
    ee.Initialize(credentials)
    logging.info("GEE initialized successfully")
except Exception as e:
    logging.warning(f"GEE Initialization issue: {e}")

DEFAULT_AOI_ASSET = os.getenv('GEE_AOI_ASSET')

RAINBOW_PALETTE = ['blue', 'cyan', 'green', 'yellow', 'red']
SCORE_VIS_PARAMS_5_POINT = {'min': 1, 'max': 5, 'palette': RAINBOW_PALETTE}

def _determine_aoi(data):
    """
    Menentukan Area of Interest (AOI) dari request data secara dinamis dan aman.
    """
    aoi_type = data.get('aoi_type', 'polygon').lower()

    # Penanganan File
    if aoi_type == 'file' and 'aoi_geometry' in data:
        geometry_data = data['aoi_geometry']
        try:
            ee_obj = ee.Geometry(geometry_data)
            if isinstance(ee_obj, ee.FeatureCollection):
                return ee_obj.geometry().dissolve()
            elif isinstance(ee_obj, ee.Feature):
                return ee_obj.geometry()
            elif isinstance(ee_obj, ee.Geometry):
                return ee_obj
            else:
                return ee.FeatureCollection(geometry_data).geometry().dissolve()
        except Exception as e:
            if 'type' in geometry_data and geometry_data['type'] == 'FeatureCollection' and 'features' in geometry_data:
                 return ee.FeatureCollection(geometry_data).geometry().dissolve()
            elif 'type' in geometry_data and geometry_data['type'] == 'Feature' and 'geometry' in geometry_data:
                return ee.Geometry(geometry_data['geometry'])
            else:
                raise Exception(f"Invalid GeoJSON structure for AOI file: {e}")

    #Ambil Input Geometry
    geom_input = data.get('geometry')
    
    if isinstance(geom_input, str):
        try:
            geom_input = loads(geom_input)
        except json.JSONDecodeError:
            raise ValueError("Invalid geometry format (not JSON format).")

    # Cegah data kosong di awal
    if not geom_input or len(geom_input) == 0:
        if DEFAULT_AOI_ASSET:
            return ee.FeatureCollection(DEFAULT_AOI_ASSET).geometry()
        else:
            raise ValueError("The area of interest (AOI) is empty. Please draw the area (point or polygon) on the map first before starting the analysis.")

    #Ekstraksi Koordinat secara Aman
    if isinstance(geom_input, dict):
        if aoi_type == 'circle' and 'coordinates' in geom_input and 'radius' in geom_input:
            return ee.Geometry.Point(geom_input['coordinates']).buffer(geom_input['radius'])
        elif 'coordinates' in geom_input:
            coords = geom_input['coordinates']
        else:
            raise ValueError("Invalid geometry dictionary structure (no 'coordinates').")
    else:
        coords = geom_input

    # Validasi tambahan jika coordinates ternyata array kosong
    if not coords or len(coords) == 0:
        if DEFAULT_AOI_ASSET:
            return ee.FeatureCollection(DEFAULT_AOI_ASSET).geometry()
        else:
            raise ValueError("The area of interest (AOI) is empty. Please draw the area on the map first before starting the analysis.")

    # Deteksi Bentuk Koordinat (Robust Parsing)
    is_flat_point = (isinstance(coords, list) and len(coords) == 2 and isinstance(coords[0], (int, float)))
    
    if is_flat_point:
        return ee.Geometry.Point(coords).buffer(1000)

    # Fungsi pembantu untuk mengekstrak titik pertama dari nested array
    def get_first_point(c):
        while isinstance(c, list) and len(c) > 0 and isinstance(c[0], list):
            c = c[0]
        return c

    # Fungsi pembantu untuk menghitung jumlah titik (vertices)
    def count_points(c):
        if isinstance(c, list) and len(c) > 0:
            if isinstance(c[0], list) and isinstance(c[0][0], list):
                return len(c[0])
            elif isinstance(c[0], list) and isinstance(c[0][0], (int, float)):
                return len(c)
        return 0

    pt_count = count_points(coords)

    # Jika koordinat hanya memiliki 1 atau 2 titik, paksa menjadi Point agar tidak gagal saat jadi Polygon
    if 0 < pt_count < 3:
        pt = get_first_point(coords)
        return ee.Geometry.Point(pt).buffer(1000)

    # Jika titik >= 3, siap untuk membuat Polygon
    if aoi_type in ['polygon', 'rectangle']:
        return ee.Geometry.Polygon(coords)
    elif aoi_type == 'point':
        return ee.Geometry.Point(get_first_point(coords)).buffer(1000)
    elif aoi_type == 'circle':
        return ee.Geometry.Point(get_first_point(coords)).buffer(5000)
    else:
        return ee.Geometry.Polygon(coords)

def _validate_aoi_size(aoi_geometry, max_area_km2=200000):
    """
    Memvalidasi ukuran AOI agar tidak melebihi batas (200.000 km2).
    """ 
    try: 
        area_sq_meters = aoi_geometry.area(maxError=1000).getInfo()
        area_sq_km = area_sq_meters / 1e6
    
        if area_sq_km > max_area_km2:
            raise ValueError(
                f"The area is too large ({area_sq_km:.2f} km²). "
                f"The maximum computational limit is {max_area_km2} km². Please narrow down your selection."
            )
        return True
    except ee.EEException as e:
        raise ValueError(f"Failed to validate the area. The geometry may be invalid: {e}")

def _cloudMaskL8_C02(image):
    """
    Masking awan untuk Landsat 8 C02.
    """
    qa = image.select('QA_PIXEL')
    dilated = 1 << 1
    cirrus = 1 << 2
    cloud = 1 << 3
    shadow = 1 << 4
    mask = (
        qa.bitwiseAnd(dilated).eq(0)
        .And(qa.bitwiseAnd(cirrus).eq(0))
        .And(qa.bitwiseAnd(cloud).eq(0))
        .And(qa.bitwiseAnd(shadow).eq(0))
    )
    
    return (
        image.select(
            ['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6', 'SR_B7'],
            ['B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7'], 
        )
        .multiply(0.0000275)
        .add(-0.2)
        .updateMask(mask)
    )

def _get_permanent_water_dist(aoi):
    """
    Menghitung jarak dari perairan permanen.
    """
    GSW = ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
    SRTM = ee.Image("USGS/SRTMGL1_003")
    water = GSW.select('occurrence').clip(aoi)
    permanent = water.gt(80) 
    distance = permanent.fastDistanceTransform().divide(30).clip(aoi)

    srtm_mask = SRTM.clip(aoi).mask()
    onlyDistance = distance.updateMask(distance.neq(0).And(srtm_mask))
    return onlyDistance

def _determine_optimal_scale(aoi_geometry):
    """
    Menentukan skala resolusi dinamis berdasarkan luas area.
    """
    try: 
        area_sq_meters = aoi_geometry.area(maxError=1000).getInfo()
        area_sq_km = area_sq_meters / 1e6
        
        if area_sq_km <= 1000:
            return 30
        elif area_sq_km <= 10000:
            return 100
        elif area_sq_km <= 50000:
            return 250
        else:
            return 500
            
    except Exception as e:
        logging.warning(f"Failed to calculate the area for the scale: {e}")
        return 250

def get_flood_risk(data):
    """
    Menghitung Flood Risk Terpadu beserta visualisasi seluruh layernya.
    """
    try:
        #Tentukan AOI dan Validasi
        try:
            aoi = _determine_aoi(data)
            _validate_aoi_size(aoi, max_area_km2=200000)
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400
        
        if not data.get('startDate') or not data.get('endDate'):
             return jsonify({'error': 'Start Date and End Date are required.'}), 400

        try:
            startDate = ee.Date(data['startDate'])
            endDate = ee.Date(data['endDate'])
        except Exception:
            return jsonify({'error': 'Invalid date format.'}), 400

        # Ekstraksi Bobot Dinamis
        weights = data.get('weights', {})
        w_rain = float(weights.get('rain', 0.30))
        w_elev = float(weights.get('elevation', 0.20))
        w_dist = float(weights.get('distance', 0.20))
        w_topo = float(weights.get('topo', 0.10))
        w_wet  = float(weights.get('wetness', 0.10))
        w_veg  = float(weights.get('vegetation', 0.10))

        total_weight = w_rain + w_elev + w_dist + w_topo + w_wet + w_veg
        if abs(total_weight - 1.0) > 0.01:
            return jsonify({'error': f'The total weight must be 1.0 (100%). Current total: {total_weight}'}), 400

        # Pengumpulan Data Base GEE (Rainfall, Landsat, SRTM)
        chirps_col = ee.ImageCollection('UCSB-CHG/CHIRPS/PENTAD').filterDate(startDate, endDate).filterBounds(aoi)
        
        if chirps_col.size().getInfo() > 0:
            total_rainfall = chirps_col.sum().clip(aoi)
            rainScore = total_rainfall.where(total_rainfall.gt(200), 5) \
                .where(total_rainfall.gt(150).And(total_rainfall.lte(200)), 4) \
                .where(total_rainfall.gt(100).And(total_rainfall.lte(150)), 3) \
                .where(total_rainfall.gt(50).And(total_rainfall.lte(100)), 2) \
                .where(total_rainfall.lte(50), 1)
        else:
            logging.warning("No CHIRPS data found for this date range. Defaulting rain score to 1.")
            total_rainfall = ee.Image.constant(0).clip(aoi)
            rainScore = ee.Image.constant(1).clip(aoi)

        SRTM = ee.Image("USGS/SRTMGL1_003")
        elevation = SRTM.clip(aoi)
        onlyDistance = _get_permanent_water_dist(aoi)
        
        l8_col = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").filterBounds(aoi).filterDate(startDate, endDate).filter(ee.Filter.lt('CLOUD_COVER', 30))
        if l8_col.size().getInfo() == 0:
             fallbackDate = ee.Date(data['endDate'])
             l8_col = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") \
                .filterBounds(aoi).filterDate(fallbackDate.advance(-1, 'year'), fallbackDate) \
                .filter(ee.Filter.lt('CLOUD_COVER', 50))
        landsat8 = l8_col.map(_cloudMaskL8_C02).median().clip(aoi)

        # Base Parameter Computation
        tpi = elevation.subtract(elevation.focalMean(5).reproject('EPSG:4326', None, 30))
        ndvi = landsat8.normalizedDifference(['B5', 'B4']).rename('NDVI')
        ndwi = landsat8.normalizedDifference(['B3', 'B5']).rename('NDWI')
        
        # Natural Flood Shape Logic (untuk display jarak)
        water_source = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select('occurrence').gt(50).unmask(0).clip(aoi)
        rawDistance = water_source.fastDistanceTransform().reproject(crs='EPSG:4326', scale=30).sqrt().multiply(30)
        slope = ee.Terrain.slope(elevation)
        naturalFloodShape = rawDistance.updateMask(
            rawDistance.lte(1000).And(rawDistance.gt(0)).And(slope.lte(6))
        )

        # Skoring
        distanceScore = onlyDistance.where(onlyDistance.gt(4000), 1).where(onlyDistance.lte(1000), 5).where(onlyDistance.gt(1000).And(onlyDistance.lte(4000)), 3)
        elevScore = elevation.where(elevation.gt(20), 1).where(elevation.lte(5), 5).where(elevation.gt(5).And(elevation.lte(20)), 3)
        topoScore = tpi.where(tpi.gt(0), 1).where(tpi.lte(-5), 5).where(tpi.gt(-5).And(tpi.lte(0)), 3)
        vegScore = ndvi.where(ndvi.gt(0.6), 1).where(ndvi.lte(0.2), 5).where(ndvi.gt(0.2).And(ndvi.lte(0.6)), 3)
        wetScore = ndwi.where(ndwi.gt(0.5), 5).where(ndwi.lte(-0.5), 1).where(ndwi.gt(-0.5).And(ndwi.lte(0.5)), 3)

        # Penerapan Bobot Dinamis & Risiko Akhir
        floodHazard = (rainScore.multiply(w_rain)) \
              .add(elevScore.multiply(w_elev)) \
              .add(distanceScore.multiply(w_dist)) \
              .add(topoScore.multiply(w_topo)) \
              .add(wetScore.multiply(w_wet)) \
              .add(vegScore.multiply(w_veg))

        finalRiskMap = floodHazard \
            .where(floodHazard.gt(3.5), 5) \
            .where(floodHazard.gt(2.9).And(floodHazard.lte(3.5)), 4) \
            .where(floodHazard.gt(2.3).And(floodHazard.lte(2.9)), 3) \
            .where(floodHazard.gt(1.7).And(floodHazard.lte(2.3)), 2) \
            .where(floodHazard.lte(1.7), 1) \
            .rename('Flood_Risk_Index')


        #Ekstraksi Data Statistik untuk Grafik
        stats_scale = _determine_optimal_scale(aoi)
        
        try:
            combined_image = ee.Image([total_rainfall, elevation, ndvi, ndwi])
            mean_stats = combined_image.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=aoi,
                scale=stats_scale,
                maxPixels=1e9
            ).getInfo()

            risk_hist = finalRiskMap.reduceRegion(
                reducer=ee.Reducer.frequencyHistogram(),
                geometry=aoi,
                scale=stats_scale,
                maxPixels=1e9
            ).getInfo()

            statistics = {
                "avg_rainfall_mm": round(mean_stats.get('precipitation', 0) or 0, 2),
                "avg_elevation_m": round(mean_stats.get('elevation', 0) or 0, 2),
                "avg_ndvi": round(mean_stats.get('NDVI', 0) or 0, 2),
                "avg_ndwi": round(mean_stats.get('NDWI', 0) or 0, 2),
                "risk_distribution": risk_hist.get('Flood_Risk_Index', {})
            }
        except Exception as e:
            logging.error(f"Failed to calculate statistics: {str(e)}")
            statistics = {}

        # Generate Semua Tile URL 
        map_urls = {
            'FloodRisk': finalRiskMap.getMapId(SCORE_VIS_PARAMS_5_POINT)['tile_fetcher'].url_format,
            'Rainfall': total_rainfall.getMapId({'min': 0, 'max': 300, 'palette': ['#FFFFFF', '#00BFFF', '#0000FF', '#00008B', '#800080']})['tile_fetcher'].url_format,
            'Elevation': elevation.getMapId({'min': 0, 'max': 100, 'palette': ['green', 'yellow', 'red', 'white']})['tile_fetcher'].url_format,
            'TPI': tpi.getMapId({'min': -5, 'max': 5, 'palette': ['blue', 'yellow', 'red']})['tile_fetcher'].url_format,
            'NDVI': ndvi.getMapId({'min': -1, 'max': 1, 'palette': ['blue', 'white', 'green']})['tile_fetcher'].url_format,
            'NDWI': ndwi.getMapId({'min': -1, 'max': 1, 'palette': ['red', 'white', 'blue']})['tile_fetcher'].url_format,
            'Distance': naturalFloodShape.getMapId({'min': 0, 'max': 1000, 'palette': ['00008B', '0000FF', '00BFFF', 'ADD8E6']})['tile_fetcher'].url_format
        }

        # Generate Legends untuk Frontend
        legends = {
            'FloodRisk': { "title": "Flood Risk", "items": [
                { "color": "#0000FF", "label": "Very Low (1)" }, 
                { "color": "#00FFFF", "label": "Low (2)" },      
                { "color": "#00FF00", "label": "Moderate (3)" },   
                { "color": "#FFFF00", "label": "High (4)" },     
                { "color": "#FF0000", "label": "Critical (5)" } 
            ]},
            'Rainfall': { "title": "Accumulated Rainfall", "items": [
                { "color": "#FFFFFF", "label": "0 mm (Dry)" },
                { "color": "#00BFFF", "label": "75 mm" },
                { "color": "#0000FF", "label": "150 mm" },
                { "color": "#00008B", "label": "225 mm" },
                { "color": "#800080", "label": "300+ mm (Extreme)" }
            ]},
            'Elevation': { "title": "Elevation (m)", "items": [
                { "color": "#FFFFFF", "label": "> 100m" },
                { "color": "#FF0000", "label": "75m" },
                { "color": "#FFFF00", "label": "50m" },
                { "color": "#008000", "label": "0m" }
            ]},
            'TPI': { "title": "TPI", "items": [
                { "color": "#FF0000", "label": "Ridge/Peak" },
                { "color": "#FFFF00", "label": "Mid-Slope" },
                { "color": "#0000FF", "label": "Valley" }
            ]},
            'NDVI': { "title": "NDVI", "items": [
                { "color": "#008000", "label": "High Vegetation" },
                { "color": "#FFFFFF", "label": "Non-Vegetation" },
                { "color": "#0000FF", "label": "Water" }
            ]},
            'NDWI': { "title": "NDWI", "items": [
                { "color": "#0000FF", "label": "Water" },
                { "color": "#FFFFFF", "label": "Neutral" },
                { "color": "#FF0000", "label": "Dry" }
            ]},
            'Distance': { "title": "Potential Flood Area", "items": [
                { "color": "#00008B", "label": "Main Water Body" },
                { "color": "#0000FF", "label": "Riverbank Area" },
                { "color": "#00BFFF", "label": "Floodplain" },
                { "color": "#ADD8E6", "label": "Max Overflow" }
            ]}
        }

        # Generasi Style SLD untuk GeoServer (1-Band: Pixel Values 1 s/d 5)
        risk_sld_rules = [
            {"quantity": 0, "color": "#000000", "label": "No Data", "opacity": 0.0},
            {"quantity": 1, "color": "#0000FF", "label": "Very Low (1)", "opacity": 1.0},
            {"quantity": 2, "color": "#00FFFF", "label": "Low (2)", "opacity": 1.0},
            {"quantity": 3, "color": "#00FF00", "label": "Moderate (3)", "opacity": 1.0},
            {"quantity": 4, "color": "#FFFF00", "label": "High (4)", "opacity": 1.0},
            {"quantity": 5, "color": "#FF0000", "label": "Critical (5)", "opacity": 1.0}
        ]
        
        # Download URL Direct Raw 1-Band GeoTIFF
        optimal_scale = _determine_optimal_scale(aoi)
        try:
            region_coords = aoi.bounds().getInfo()['coordinates']
            
            # Konversi ke Byte 1-band agar bernilai integer presisi 1-5
            download_url = finalRiskMap.toByte().getDownloadURL({
                'scale': optimal_scale, 
                'crs': 'EPSG:4326',
                'region': region_coords,
                'format': 'GEO_TIFF'
            })
        except Exception as e:
            logging.error(f"Failed to generate download URL: {str(e)}")
            download_url = None

        sld_xml = generate_raster_sld("FloodRisk", "values", risk_sld_rules)

        # Return Object
        return jsonify({
            'maps': map_urls,
            'legends': legends,
            'statistics': statistics,
            'download_url': download_url,
            'style_sld': sld_xml
        })

    except Exception as e:
        logging.error(f"Error in get_flood_risk: {str(e)}", exc_info=True)
        return jsonify({'error': 'An internal error occurred.'}), 500