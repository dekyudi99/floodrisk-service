from flask import Blueprint, jsonify, request, send_file
import rasterio as rio
from rasterio.io import MemoryFile
import numpy as np
from matplotlib.colors import from_levels_and_colors
import requests
from io import BytesIO
from app.services.floodRisk_services import get_flood_risk
from app.services.floodEvent_services import get_flood_event
from app.services.rainfall_services import get_rainfall
from app.services.component_services import get_component_analysis

api_bp = Blueprint('main', __name__)

@api_bp.route('/test', methods=['GET'])
def home():
    return jsonify({"message": "Flask Server Run Successfully"})

@api_bp.route('/api/v1/compute', methods=['POST'])
def compute_analysis():
    # Tangkap payload JSON dari Laravel
    data = request.get_json()
    print(data)
    print("JSON payload")

    if not data:
        print("JSON payload not found")
        return jsonify({"error": "JSON payload not found"}), 400

    # Ambil jenis analisis yang diminta user
    analysis_type = data.get('analysis_type', '')

    if analysis_type == 'flood_risk':
        return get_flood_risk(data)
    elif analysis_type == 'flood_event':
        return get_flood_event(data)
    elif analysis_type == 'rainfall':
        return get_rainfall(data)
    elif (
        analysis_type.startswith('component_') or 
        analysis_type in ['component', 'elevation', 'distance', 'tpi', 'ndvi', 'ndwi', 'component_rainfall', 'component_elevation', 'component_distance', 'component_tpi', 'component_ndvi', 'component_ndwi']
    ):
        return get_component_analysis(data)
    elif analysis_type in ['land_cover']:
        return jsonify({"error": f"Services for analysis '{analysis_type}' is currently under development."}), 501
    else:
        return jsonify({"error": f"Types of analysis '{analysis_type}' invalid or unsupported."}), 400

@api_bp.route('/api/download/colored', methods=['GET'])
def download_colored_tiff():
    raw_url = request.args.get('url')
    analysis_type = request.args.get('type')

    if not raw_url:
        return {"error": "Missing raw GEE URL"}, 400

    try:
        # Download RAW GeoTIFF dari GEE ke memori RAM
        res = requests.get(raw_url, timeout=30)
        res.raise_for_status()
        raw_tiff_bytes = res.content
    except Exception as e:
        return {"error": f"Failed to download raw TIFF: {str(e)}"}, 500

    if analysis_type == 'FloodRisk':
        classes = [1, 2, 3, 4, 5, 6] 
        palette = ['#0000FF', '#00FFFF', '#00FF00', '#FFFF00', '#FF0000']
    elif analysis_type == 'FloodEvent':
        classes = [0.5, 1.5]
        palette = ['#D60000']
    elif analysis_type == 'Rainfall':
        classes = [0, 2, 4, 6, 8, 100]
        palette = ['#001137', '#0aab1e', '#e7eb05', '#ff4a2d', '#e90000']
    else:
        # Langsung kirim raw_tiff_bytes dari GEE
        return send_file(
            BytesIO(raw_tiff_bytes),
            mimetype='image/tiff',
            as_attachment=True,
            download_name=f"{analysis_type}_Raw.tif"
        )

    # Proses Rasterio di Memori
    try:
        with MemoryFile(raw_tiff_bytes) as memfile:
            with memfile.open() as src:
                raw_data = src.read(1)# Ambil 1 band asli
                profile = src.profile.copy()
                
                # Update profile GeoTIFF tetap 1 Band
                profile.update(
                    count=1,
                    dtype=src.dtypes[0],         
                    nodata=src.nodata if src.nodata is not None else 0,
                    compress='lzw'
                )

                profile.pop('photometric', None)

        # Tulis hasil 1 band ke file memori dan kirim ke User
        out_memfile = MemoryFile()
        with out_memfile.open(**profile) as dst:
            dst.write(raw_data, 1)               
        
        out_memfile.seek(0)
        return send_file(
            out_memfile, 
            mimetype='image/tiff', 
            as_attachment=True, 
            download_name=f"{analysis_type}_1Band.tif"
        )

    except Exception as e:
        return {"error": f"Failed to process TIFF: {str(e)}"}, 500


# @api_bp.route('/api/download/colored', methods=['GET'])
# def download_colored_tiff():
#     raw_url = request.args.get('url')
#     analysis_type = request.args.get('type')

#     if not raw_url:
#         return {"error": "Missing raw GEE URL"}, 400

#     try:
#         # Download RAW GeoTIFF dari GEE ke memori RAM
#         res = requests.get(raw_url, timeout=30)
#         res.raise_for_status()
#         raw_tiff_bytes = res.content
#     except Exception as e:
#         return {"error": f"Failed to download raw TIFF: {str(e)}"}, 500

#     if analysis_type == 'FloodRisk':
#         classes = [1, 2, 3, 4, 5, 6] 
#         palette = ['#0000FF', '#00FFFF', '#00FF00', '#FFFF00', '#FF0000']
#     elif analysis_type == 'FloodEvent':
#         classes = [0.5, 1.5]
#         palette = ['#D60000']
#     elif analysis_type == 'Rainfall':
#         classes = [0, 2, 4, 6, 8, 100]
#         palette = ['#001137', '#0aab1e', '#e7eb05', '#ff4a2d', '#e90000']
#     else:
#         # Langsung kirim raw_tiff_bytes dari GEE
#         return send_file(
#             BytesIO(raw_tiff_bytes),
#             mimetype='image/tiff',
#             as_attachment=True,
#             download_name=f"{analysis_type}_Raw.tif"
#         )

#     # Proses Rasterio & Matplotlib di Memori (referensi= FinalRun.ipynb)
#     # try:
#     #     with MemoryFile(raw_tiff_bytes) as memfile:
#     #         with memfile.open() as src:
#     #             raw_data = src.read(1)
#     #             profile = src.profile
                
#     #             # Buat Alpha mask (0 jika NoData, 255 jika ada data)
#     #             nodata_val = src.nodata if src.nodata is not None else 0
#     #             alpha_mask = np.where(raw_data == nodata_val, 0, 255).astype(np.uint8)

#     #             # Terapkan Pewarnaan Matplotlib
#     #             cmap, norm = from_levels_and_colors(classes, palette)
#     #             colors_rgba = cmap(norm(raw_data)) # Menghasilkan float 0-1
#     #             colors_rgb = (colors_rgba[:, :, :3] * 255).astype(np.uint8)

#     #             # Gabungkan RGB dan Alpha
#     #             rgba = np.zeros((raw_data.shape[0], raw_data.shape[1], 4), dtype=np.uint8)
#     #             rgba[:, :, :3] = colors_rgb
#     #             rgba[:, :, 3] = alpha_mask
                
#     #             # Ubah shape untuk Rasterio: (Bands, H, W)
#     #             rgba = rgba.transpose(2, 0, 1)

#     #             # Update profile GeoTIFF menjadi RGBA (4 Band)
#     #             # Suntikkan photometric='RGB' di sini agar QGIS membacanya sebagai citra warna
#     #             profile.update(
#     #                 count=4, 
#     #                 dtype='uint8', 
#     #                 nodata=0, 
#     #                 compress='lzw',
#     #                 photometric='RGB' 
#     #             )

#     #     # Tulis hasil ke file memori baru dan kirim ke User
#     #     out_memfile = MemoryFile()
#     #     with out_memfile.open(**profile) as dst:
#     #         dst.write(rgba)
        
#     #     out_memfile.seek(0)
#     #     return send_file(
#     #         out_memfile, 
#     #         mimetype='image/tiff', 
#     #         as_attachment=True, 
#     #         download_name=f"{analysis_type}_Colored.tif"
#     #     )

#     # except Exception as e:
#     #     return {"error": f"Failed to colorize TIFF: {str(e)}"}, 500
#         # Proses Rasterio di Memori
#     try:
#         with MemoryFile(raw_tiff_bytes) as memfile:
#             with memfile.open() as src:
#                 raw_data = src.read(1)          # Ambil 1 band asli
#                 profile = src.profile.copy()
                
#                 # Update profile GeoTIFF tetap 1 Band
#                 profile.update(
#                     count=1,
#                     dtype=src.dtypes[0],         # Pertahankan tipe data asli (misal: uint8 / int16 / float32)
#                     nodata=src.nodata if src.nodata is not None else 0,
#                     compress='lzw'
#                 )

#                 # Pastikan tidak ada photometric RGB karena ini 1 band
#                 profile.pop('photometric', None)

#         # Tulis hasil 1 band ke file memori dan kirim ke User
#         out_memfile = MemoryFile()
#         with out_memfile.open(**profile) as dst:
#             dst.write(raw_data, 1)               # Tulis band 1
        
#         out_memfile.seek(0)
#         return send_file(
#             out_memfile, 
#             mimetype='image/tiff', 
#             as_attachment=True, 
#             download_name=f"{analysis_type}_1Band.tif"
#         )

#     except Exception as e:
#         return {"error": f"Failed to process TIFF: {str(e)}"}, 500
