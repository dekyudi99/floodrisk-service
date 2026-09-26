import ee
import logging
import os
import urllib.parse
from flask import jsonify
from app.services.floodRisk_services import _determine_aoi, _validate_aoi_size, _determine_optimal_scale
from app.helpers.sld_generator import generate_raster_sld


DEFAULT_AOI_ASSET = os.getenv('GEE_AOI_ASSET')

def get_flood_event(data):
    """
    Menghitung Flood Event (Inundasi) menggunakan Sentinel-1 
    dengan output terstandarisasi.
    """
    try:
        if 'after_startDate' not in data or 'after_endDate' not in data:
            return jsonify({'error': 'The after_startDate and after_endDate parameters are required.'}), 400
        
        after_startDate = data['after_startDate']
        after_endDate = data['after_endDate']

        # Tentukan AOI dan Validasi
        try:
            aoi = _determine_aoi(data)
            _validate_aoi_size(aoi, max_area_km2=200000)
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400

        # Proses Sentinel-1
        before_startDate = ee.Date(after_startDate).advance(-3, 'month')
        before_endDate = ee.Date(after_startDate)
        
        collection = (
            ee.ImageCollection('COPERNICUS/S1_GRD')
            .filter(ee.Filter.eq('instrumentMode', 'IW'))
            .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
            .filter(ee.Filter.eq('orbitProperties_pass', 'DESCENDING'))
            .filterBounds(aoi)
            .select(['VV'])
        )
        
        before_collection = collection.filterDate(before_startDate, before_endDate)
        after_collection = collection.filterDate(after_startDate, after_endDate)
        
        if after_collection.size().getInfo() == 0:
            return jsonify({'error': 'There are no Sentinel-1 images for that date range.'}), 404

        before_img = before_collection.mosaic().clip(aoi)
        after_img = after_collection.mosaic().clip(aoi)

        smoothing_radius = 50
        before_filtered = before_img.focal_mean(smoothing_radius, 'circle', 'meters')
        after_filtered = after_img.focal_mean(smoothing_radius, 'circle', 'meters')
        
        FLOOD_THRESHOLD = -16
        water_before = before_filtered.lt(FLOOD_THRESHOLD)
        water_after = after_filtered.lt(FLOOD_THRESHOLD)
        
        flooded_area = water_after.And(water_before.Not())
        
        dem = ee.Image('USGS/SRTMGL1_003')
        slope = ee.Terrain.slope(dem)
        flooded_area = flooded_area.updateMask(slope.lt(10))

        # Format Response: Maps
        response_map = {
            'WaterBefore': water_before.selfMask().getMapId({'palette': ["#12A1D5"]})['tile_fetcher'].url_format,
            'WaterAfter': water_after.selfMask().getMapId({'palette': ["#002287"]})['tile_fetcher'].url_format,
            'NewFloodedArea': flooded_area.selfMask().getMapId({'palette': ["#D60000"]})['tile_fetcher'].url_format
        }

        # Format Response: Legends
        legends = {
            'FloodEvent': {
                "title": "Flood Comparison",
                "items": [
                    { "color": "#12A1D5", "label": "Water Before Event" },
                    { "color": "#002287", "label": "Water During Event" },
                    { "color": "#D60000", "label": "New Flooded Area" }
                ]
            }
        }

        # Format Response: Statistics (Area calculation)
        stats_scale = _determine_optimal_scale(aoi)
        pixel_area = ee.Image.pixelArea()
        flood_area_calc = flooded_area.multiply(pixel_area).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=aoi,
            scale=stats_scale,
            maxPixels=1e9
        ).getInfo()

        statistics = {
            "new_flooded_area_sqm": round(flood_area_calc.get('VV', 0) or 0, 2),
            "new_flooded_area_km2": round((flood_area_calc.get('VV', 0) or 0) / 1e6, 2)
        }

        # Generasi Style SLD untuk GeoServer (1-Band: Pixel = 1)
        sld_rules = [
            {"quantity": 0, "color": "#000000", "label": "Non-Flooded", "opacity": 0.0},
            {"quantity": 1, "color": "#D60000", "label": "New Flooded Area", "opacity": 1.0}
        ]
        
        # Format Response: Download URL Raw 1-Band GeoTIFF Langsung dari GEE
        try:
            region_coords = aoi.bounds().getInfo()['coordinates']
            
            # Konversi ke Byte 1-band (Nilai pixel: 1 untuk area banjir)
            download_url = flooded_area.toByte().getDownloadURL({
                'scale': stats_scale, 
                'crs': 'EPSG:4326',
                'region': region_coords,
                'format': 'GEO_TIFF'
            })
        except Exception as e:
            logging.error(f"Failed to generate download URL Flood Event: {str(e)}")
            download_url = None

        sld_xml = generate_raster_sld("FloodEvent", "values", sld_rules)

        return jsonify({
            'maps': response_map,
            'legends': legends,
            'statistics': statistics,
            'download_url': download_url,
            'style_sld': sld_xml
        })
    
    except Exception as e:
        logging.error(f"Error in get_flood_event: {str(e)}", exc_info=True)
        return jsonify({'error': 'An internal error has occurred.'}), 500