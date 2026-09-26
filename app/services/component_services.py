import ee
import os
import json
from json import loads
import logging
from flask import jsonify
from app.helpers.sld_generator import generate_raster_sld
from app.services.floodRisk_services import (
    _determine_aoi,
    _validate_aoi_size,
    _determine_optimal_scale,
    _get_permanent_water_dist,
    _cloudMaskL8_C02
)

def get_component_analysis(data):
    """
    Menghitung analisis komponen Flood Risk secara mandiri (Rainfall, Elevation, Distance, TPI, NDVI, NDWI)
    dan mengembalikan GeoTIFF single-band direct download URL, SLD XML, legends, dan statistik.
    """
    try:
        component = data.get('component') or data.get('analysis_type', '').replace('component_', '')
        component = component.lower()

        # Validasi AOI
        try:
            aoi = _determine_aoi(data)
            _validate_aoi_size(aoi, max_area_km2=200000)
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400

        start_date_str = data.get('startDate') or data.get('start_date') or '2024-01-01'
        end_date_str = data.get('endDate') or data.get('end_date') or '2024-01-31'

        try:
            startDate = ee.Date(start_date_str)
            endDate = ee.Date(end_date_str)
        except Exception:
            startDate = ee.Date('2024-01-01')
            endDate = ee.Date('2024-01-31')

        optimal_scale = _determine_optimal_scale(aoi)
        stats_scale = optimal_scale
        region_coords = aoi.bounds().getInfo()['coordinates']

        target_image = None
        layer_name = ""
        sld_rules = []
        color_map_type = "ramp"
        legend_info = {}
        statistics = {}
        tile_url = None

        if component == 'rainfall':
            layer_name = "Rainfall"
            chirps_col = ee.ImageCollection('UCSB-CHG/CHIRPS/PENTAD').filterDate(startDate, endDate).filterBounds(aoi)
            if chirps_col.size().getInfo() > 0:
                total_rainfall = chirps_col.sum().clip(aoi)
            else:
                total_rainfall = ee.Image.constant(0).clip(aoi)

            target_image = total_rainfall.toFloat().rename('Rainfall')

            mean_stat = total_rainfall.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=aoi, scale=stats_scale, maxPixels=1e9
            ).getInfo()
            max_stat = total_rainfall.reduceRegion(
                reducer=ee.Reducer.max(), geometry=aoi, scale=stats_scale, maxPixels=1e9
            ).getInfo()

            avg_val = round(list(mean_stat.values())[0] if mean_stat else 0, 2)
            max_val = round(list(max_stat.values())[0] if max_stat else 0, 2)
            statistics = {
                "avg_rainfall_mm": avg_val,
                "max_rainfall_mm": max_val,
                "total_accumulated_rainfall_mm": avg_val
            }

            sld_rules = [
                {"quantity": 0, "color": "#E0F3F8", "label": "0 mm (Kering)", "opacity": 0.7},
                {"quantity": 10, "color": "#67A9CF", "label": "10 mm (Rendah)", "opacity": 0.75},
                {"quantity": 30, "color": "#1C9099", "label": "30 mm (Sedang)", "opacity": 0.8},
                {"quantity": 60, "color": "#016C59", "label": "60 mm (Tinggi)", "opacity": 0.85},
                {"quantity": 100, "color": "#014636", "label": "100+ mm (Sangat Tinggi)", "opacity": 0.95}
            ]

            legend_info = {
                "title": "Accumulated Rainfall (mm)",
                "items": [
                    { "color": "#E0F3F8", "label": "0 mm (Kering)" },
                    { "color": "#67A9CF", "label": "10 mm (Rendah)" },
                    { "color": "#1C9099", "label": "30 mm (Sedang)" },
                    { "color": "#016C59", "label": "60 mm (Tinggi)" },
                    { "color": "#014636", "label": "100+ mm (Sangat Tinggi)" }
                ]
            }

            tile_url = target_image.getMapId({
                'min': 0, 'max': 100, 'palette': ['#E0F3F8', '#67A9CF', '#1C9099', '#016C59', '#014636']
            })['tile_fetcher'].url_format

        elif component == 'elevation':
            layer_name = "Elevation"
            SRTM = ee.Image("USGS/SRTMGL1_003")
            elevation = SRTM.clip(aoi)
            target_image = elevation.toFloat().rename('Elevation')

            stats = elevation.reduceRegion(
                reducer=ee.Reducer.minMax().combine(ee.Reducer.mean(), '', True),
                geometry=aoi, scale=stats_scale, maxPixels=1e9
            ).getInfo()

            statistics = {
                "min_elevation_m": round(stats.get('elevation_min', 0) or 0, 1),
                "avg_elevation_m": round(stats.get('elevation_mean', 0) or 0, 1),
                "max_elevation_m": round(stats.get('elevation_max', 0) or 0, 1)
            }

            sld_rules = [
                {"quantity": 0, "color": "#008000", "label": "0m (Dataran Rendah)", "opacity": 0.9},
                {"quantity": 25, "color": "#7CFC00", "label": "25m", "opacity": 0.9},
                {"quantity": 50, "color": "#FFFF00", "label": "50m (Sedang)", "opacity": 0.9},
                {"quantity": 75, "color": "#FF8C00", "label": "75m", "opacity": 0.9},
                {"quantity": 100, "color": "#FF0000", "label": "100m (Dataran Tinggi)", "opacity": 0.9},
                {"quantity": 200, "color": "#FFFFFF", "label": "200m+ (Pegunungan)", "opacity": 0.9}
            ]

            legend_info = {
                "title": "Elevation (DEM)",
                "items": [
                    { "color": "#008000", "label": "0m (Dataran Rendah)" },
                    { "color": "#FFFF00", "label": "50m (Sedang)" },
                    { "color": "#FF0000", "label": "100m (Dataran Tinggi)" },
                    { "color": "#FFFFFF", "label": "200m+ (Pegunungan)" }
                ]
            }

            tile_url = target_image.getMapId({
                'min': 0, 'max': 100, 'palette': ['#008000', '#FFFF00', '#FF0000', '#FFFFFF']
            })['tile_fetcher'].url_format

        elif component in ['distance', 'distance_from_water', 'water_distance']:
            layer_name = "Distance from Water"
            water_source = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select('occurrence').gt(30).unmask(0).clip(aoi)
            rawDistance = water_source.fastDistanceTransform().reproject(crs='EPSG:4326', scale=30).sqrt().multiply(30).clip(aoi)
            target_image = rawDistance.toFloat().rename('Distance')

            mean_stat = rawDistance.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=aoi, scale=stats_scale, maxPixels=1e9
            ).getInfo()
            statistics = {
                "avg_distance_m": round(list(mean_stat.values())[0] if mean_stat else 0, 1),
                "max_buffer_m": 3000
            }

            sld_rules = [
                {"quantity": 0, "color": "#00008B", "label": "Badan Air (0m)", "opacity": 0.95},
                {"quantity": 300, "color": "#0000FF", "label": "Bantaran Sungai (0 - 300m)", "opacity": 0.85},
                {"quantity": 800, "color": "#00BFFF", "label": "Dataran Banjir (300 - 800m)", "opacity": 0.75},
                {"quantity": 1500, "color": "#ADD8E6", "label": "Luapan Sedang (800 - 1500m)", "opacity": 0.65},
                {"quantity": 3000, "color": "#E0FFFF", "label": "Jauh dari Air (1500m+)", "opacity": 0.5}
            ]

            legend_info = {
                "title": "Distance from Water",
                "items": [
                    { "color": "#00008B", "label": "Badan Air (0m)" },
                    { "color": "#0000FF", "label": "Bantaran Sungai" },
                    { "color": "#00BFFF", "label": "Dataran Banjir" },
                    { "color": "#ADD8E6", "label": "Luapan Sedang" },
                    { "color": "#E0FFFF", "label": "Jauh dari Air" }
                ]
            }

            tile_url = target_image.getMapId({
                'min': 0, 'max': 2000, 'palette': ['#00008B', '#0000FF', '#00BFFF', '#ADD8E6', '#E0FFFF']
            })['tile_fetcher'].url_format

        elif component == 'tpi':
            layer_name = "Topographic Position Index"
            SRTM = ee.Image("USGS/SRTMGL1_003")
            elevation = SRTM.clip(aoi)
            tpi = elevation.subtract(elevation.focalMean(5).reproject('EPSG:4326', None, 30))
            target_image = tpi.toFloat().rename('TPI')

            mean_stat = tpi.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=aoi, scale=stats_scale, maxPixels=1e9
            ).getInfo()
            statistics = {
                "avg_tpi": round(list(mean_stat.values())[0] if mean_stat else 0, 2)
            }

            sld_rules = [
                {"quantity": -10, "color": "#0000FF", "label": "Lembah Dalam (-10 ke bawah)", "opacity": 0.9},
                {"quantity": -5, "color": "#00BFFF", "label": "Lembah (-5)", "opacity": 0.85},
                {"quantity": 0, "color": "#FFFF00", "label": "Datar / Lereng Tengah (0)", "opacity": 0.8},
                {"quantity": 5, "color": "#FF8C00", "label": "Punggung Bukit (5)", "opacity": 0.85},
                {"quantity": 10, "color": "#FF0000", "label": "Puncak Bukit (10 ke atas)", "opacity": 0.9}
            ]

            legend_info = {
                "title": "Topographic Position Index (TPI)",
                "items": [
                    { "color": "#0000FF", "label": "Lembah" },
                    { "color": "#FFFF00", "label": "Datar / Lereng" },
                    { "color": "#FF0000", "label": "Puncak Bukit" }
                ]
            }

            tile_url = target_image.getMapId({
                'min': -5, 'max': 5, 'palette': ['#0000FF', '#FFFF00', '#FF0000']
            })['tile_fetcher'].url_format

        elif component == 'ndvi':
            layer_name = "Vegetation (NDVI)"
            l8_col = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").filterBounds(aoi).filterDate(startDate, endDate).filter(ee.Filter.lt('CLOUD_COVER', 40))
            if l8_col.size().getInfo() == 0:
                fallbackDate = endDate
                l8_col = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") \
                    .filterBounds(aoi).filterDate(fallbackDate.advance(-1, 'year'), fallbackDate) \
                    .filter(ee.Filter.lt('CLOUD_COVER', 60))
            landsat8 = l8_col.map(_cloudMaskL8_C02).median().clip(aoi)
            ndvi = landsat8.normalizedDifference(['B5', 'B4']).rename('NDVI')
            target_image = ndvi.toFloat()

            mean_stat = ndvi.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=aoi, scale=stats_scale, maxPixels=1e9
            ).getInfo()
            avg_ndvi = round((list(mean_stat.values())[0] if mean_stat else 0) or 0, 2)
            statistics = {
                "avg_ndvi": avg_ndvi
            }

            sld_rules = [
                {"quantity": -1.0, "color": "#0000FF", "label": "Air (di bawah 0)", "opacity": 0.8},
                {"quantity": 0.0, "color": "#D2B48C", "label": "Tanah Terbuka (0.0)", "opacity": 0.85},
                {"quantity": 0.2, "color": "#FFFDD0", "label": "Non-Vegetasi (0.0 - 0.2)", "opacity": 0.85},
                {"quantity": 0.5, "color": "#90EE90", "label": "Vegetasi Sedang (0.2 - 0.5)", "opacity": 0.9},
                {"quantity": 0.8, "color": "#008000", "label": "Vegetasi Lebat (0.5 ke atas)", "opacity": 0.95}
            ]

            legend_info = {
                "title": "Vegetation Index (NDVI)",
                "items": [
                    { "color": "#0000FF", "label": "Air" },
                    { "color": "#FFFDD0", "label": "Non-Vegetasi / Tanah" },
                    { "color": "#008000", "label": "Vegetasi Lebat" }
                ]
            }

            tile_url = target_image.getMapId({
                'min': -0.2, 'max': 0.8, 'palette': ['#0000FF', '#FFFDD0', '#90EE90', '#008000']
            })['tile_fetcher'].url_format

        elif component == 'ndwi':
            layer_name = "Wetness (NDWI)"
            l8_col = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").filterBounds(aoi).filterDate(startDate, endDate).filter(ee.Filter.lt('CLOUD_COVER', 40))
            if l8_col.size().getInfo() == 0:
                fallbackDate = endDate
                l8_col = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") \
                    .filterBounds(aoi).filterDate(fallbackDate.advance(-1, 'year'), fallbackDate) \
                    .filter(ee.Filter.lt('CLOUD_COVER', 60))
            landsat8 = l8_col.map(_cloudMaskL8_C02).median().clip(aoi)
            ndwi = landsat8.normalizedDifference(['B3', 'B5']).rename('NDWI')
            target_image = ndwi.toFloat()

            mean_stat = ndwi.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=aoi, scale=stats_scale, maxPixels=1e9
            ).getInfo()
            avg_ndwi = round((list(mean_stat.values())[0] if mean_stat else 0) or 0, 2)
            statistics = {
                "avg_ndwi": avg_ndwi
            }

            sld_rules = [
                {"quantity": -1.0, "color": "#D73027", "label": "Lahan Kering (di bawah -0.2)", "opacity": 0.85},
                {"quantity": -0.2, "color": "#FC8D59", "label": "Kelembapan Rendah (-0.2 - 0.0)", "opacity": 0.85},
                {"quantity": 0.0, "color": "#FEE08B", "label": "Netral (0.0)", "opacity": 0.85},
                {"quantity": 0.3, "color": "#91BFDB", "label": "Lembab / Basah (0.1 - 0.3)", "opacity": 0.9},
                {"quantity": 1.0, "color": "#4575B4", "label": "Badan Air (0.3 ke atas)", "opacity": 0.95}
            ]

            legend_info = {
                "title": "Water Index (NDWI)",
                "items": [
                    { "color": "#D73027", "label": "Lahan Kering" },
                    { "color": "#FEE08B", "label": "Netral" },
                    { "color": "#4575B4", "label": "Badan Air" }
                ]
            }

            tile_url = target_image.getMapId({
                'min': -0.5, 'max': 0.5, 'palette': ['#D73027', '#FC8D59', '#FEE08B', '#91BFDB', '#4575B4']
            })['tile_fetcher'].url_format

        else:
            return jsonify({'error': f"Unknown component '{component}'"}), 400

        # Generate direct download URL GeoTIFF (1-band float)
        try:
            download_url = target_image.getDownloadURL({
                'scale': optimal_scale,
                'crs': 'EPSG:4326',
                'region': region_coords,
                'format': 'GEO_TIFF'
            })
        except Exception as e:
            logging.error(f"Failed to generate download URL for component {component}: {str(e)}")
            download_url = None

        sld_xml = generate_raster_sld(layer_name, color_map_type, sld_rules)

        maps = {
            layer_name: tile_url
        }
        legends = {
            layer_name: legend_info
        }

        return jsonify({
            'component': component,
            'layer_name': layer_name,
            'maps': maps,
            'legends': legends,
            'statistics': statistics,
            'download_url': download_url,
            'style_sld': sld_xml
        })

    except Exception as e:
        logging.error(f"Error in get_component_analysis: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to compute component: {str(e)}'}), 500
