import ee
import logging
import pandas as pd
from datetime import datetime
import urllib.parse
from flask import jsonify
from app.helpers import fc_to_dict, generate_raster_sld
import json

from app.services.floodRisk_services import _determine_aoi, _validate_aoi_size, _determine_optimal_scale
from app.helpers.sld_generator import generate_raster_sld

def get_rainfall(data):
    """
    Mengambil data presipitasi harian CHIRPS.
    """
    try:

        start_date_str = data.get('startDate')
        end_date_str = data.get('endDate')

        if not start_date_str and data.get('startYear'):
            # Konversi format tahun menjadi string tanggal penuh (YYYY-MM-DD)
            start_date_str = f"{data.get('startYear')}-01-01"
            end_date_str = f"{data.get('endYear')}-12-31"

        if not start_date_str or not end_date_str:
            return jsonify({'error': 'The date parameter is invalid. Be sure to include either startDate or startYear.'}), 400

        
        #Tentukan AOI dan Validasi
        try:
            aoi = _determine_aoi(data)
            _validate_aoi_size(aoi, max_area_km2=200000)
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400

        startDate = ee.Date(start_date_str)
        endDate = ee.Date(end_date_str)
        
        #Ambil Koleksi CHIRPS Daily
        CHIRPS_DAILY_ASSET = 'UCSB-CHG/CHIRPS/DAILY' 
        dailyPrecipCollection = (
            ee.ImageCollection(CHIRPS_DAILY_ASSET)
            .filterDate(startDate, endDate)
            .filterBounds(aoi)
            .select('precipitation') 
        )

        if dailyPrecipCollection.size().getInfo() == 0:
            return jsonify({'error': 'There is no daily precipitation data for the AOI and that time period.'}), 404

        #Proses Statistik Deret Waktu (Time Series)
        def extract_daily_data(image):
            stats = image.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=aoi,
                scale=5000,
                maxPixels=1e7
            )
            time_start = image.get('system:time_start')
            properties = {'precipitation': stats.get('precipitation')}
            return ee.Feature(None, properties).set('system:time_start', time_start)

        data_fc = ee.FeatureCollection(dailyPrecipCollection.map(extract_daily_data))
        data_dict = fc_to_dict(data_fc)
        data_df = pd.DataFrame(data_dict)
        
        if data_df.empty:
             return jsonify({'error': 'Failed to process the precipitation time series.'}), 404
             
        data_df['timestamp'] = data_df['system:time_start'].apply(lambda x: int(x))
        data_df['date'] = data_df['system:time_start'].apply(lambda x: datetime.fromtimestamp(x / 1000).strftime('%Y-%m-%d'))
        data_df = data_df[['date', 'timestamp', 'precipitation']].sort_values(by='timestamp').reset_index(drop=True)
        data_df['precipitation'] = data_df['precipitation'].fillna(0).round(2)
        
        # time_series_data = data_df.to_dict(orient="records")

        time_series_data = json.loads(data_df.to_json(orient="records"))
        #Format Response: Maps
        mean_rainfall_map = dailyPrecipCollection.mean().clip(aoi)
        precipVis = {'min': 0, 'max': 10, 'palette': ['001137', '0aab1e', 'e7eb05', 'ff4a2d', 'e90000']}
        
        maps = {
            'DailyPrecipitation': mean_rainfall_map.getMapId(precipVis)['tile_fetcher'].url_format
        }

        #Format Response: Legends
        legends = {
            'Rainfall': {
                "title": "Avg. Daily Rainfall (mm)",
                "items": [
                    { "color": "#001137", "label": "0 - 2" },
                    { "color": "#0aab1e", "label": "2 - 4" },
                    { "color": "#e7eb05", "label": "4 - 6" },
                    { "color": "#ff4a2d", "label": "6 - 8" },
                    { "color": "#e90000", "label": "8+" }
                ]
            }
        }

        total_rainfall_sum = float(data_df['precipitation'].sum())
        
        statistics = {
            "total_accumulated_rainfall_mm": round(total_rainfall_sum, 2),
            "time_series_data": time_series_data
        }

        # Generasi Style SLD Gradien Ramp untuk GeoServer (Nilai mm kontinu)
        rainfall_sld_rules = [
            {"quantity": 0, "color": "#001137", "label": "0 - 2 mm"},
            {"quantity": 2, "color": "#0aab1e", "label": "2 - 4 mm"},
            {"quantity": 4, "color": "#e7eb05", "label": "4 - 6 mm"},
            {"quantity": 6, "color": "#ff4a2d", "label": "6 - 8 mm"},
            {"quantity": 8, "color": "#e90000", "label": "8+ mm"}
        ]
        # style_sld = generate_raster_sld("Rainfall", "ramp", rainfall_sld_rules)

        optimal_scale = _determine_optimal_scale(aoi)
        try:
            region_coords = aoi.bounds().getInfo()['coordinates']
            
            # Export Raw Float 1-band
            download_url = mean_rainfall_map.toFloat().getDownloadURL({
                'scale': optimal_scale, 
                'crs': 'EPSG:4326',
                'region': region_coords,
                'format': 'GEO_TIFF'
            })
        except Exception as e:
            logging.error(f"Failed to generate the Rainfall download URL: {str(e)}")
            download_url = None

        sld_xml = generate_raster_sld("Rainfall", "ramp", rainfall_sld_rules )

        return jsonify({
            'maps': maps,
            'legends': legends,
            'statistics': statistics,
            'download_url': download_url,
            'style_sld': sld_xml
        })
    
    except Exception as e:
        logging.error(f"Error in get_rainfall: {str(e)}", exc_info=True)
        return jsonify({'error': 'An internal error occurred while processing the precipitation.'}), 500