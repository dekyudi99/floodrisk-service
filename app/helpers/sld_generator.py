import html
import re

def generate_raster_sld(layer_name, color_map_type, color_rules):
    # Bersihkan nama layer untuk tag XML Name agar valid di GeoServer
    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', str(layer_name)).strip('_') or "raster_layer"
    
    entries = ""
    for r in color_rules:
        opacity = r.get("opacity", 1.0)
        # Escape karakter khusus XML seperti <, >, &, ", '
        label = html.escape(str(r.get("label", "")))
        entries += f'                  <ColorMapEntry color="{r["color"]}" quantity="{r["quantity"]}" opacity="{opacity}" label="{label}"/>\n'
    
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0" 
  xmlns="http://www.opengis.net/sld" 
  xmlns:ogc="http://www.opengis.net/ogc" 
  xmlns:xlink="http://www.w3.org/1999/xlink" 
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" 
  xsi:schemaLocation="http://www.opengis.net/sld GetMap.xsd">
  <NamedLayer>
    <Name>{clean_name}</Name>
    <UserStyle>
      <Title>{clean_name} Style</Title>
      <FeatureTypeStyle>
        <Rule>
          <RasterSymbolizer>
            <ColorMap type="{color_map_type}">
{entries}            </ColorMap>
          </RasterSymbolizer>
        </Rule>
      </FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>"""