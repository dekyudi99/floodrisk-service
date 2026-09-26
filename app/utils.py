import ee

def fc_to_dict(fc):
    """Converting Earth Engine FeatureCollection to Dictionary."""

    prop_names = fc.first().propertyNames()
    prop_lists = fc.reduceColumns(
        reducer=ee.Reducer.toList().repeat(prop_names.size()),
        selectors=prop_names
    ).get('list')
    return ee.Dictionary.fromLists(prop_names, prop_lists)