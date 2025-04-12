'''
Created on 29 Nov 2024

@author: goktug.yaman
'''
hana_config = {
 "address": hostprodhana,
 "port": portprodhana,  
 "user": userprodhana,
 "password": passwordprodhana
}
api_key = "XXXX"

import pandas as pd
from hdbcli import dbapi
import requests

# Bounding box for Europe (approximate lat/lon ranges)
eu_lat_min, eu_lat_max = 35.0, 71.0  # Latitude range for Europe
eu_lon_min, eu_lon_max = -25.0, 50.0  # Longitude range for Europe

def is_in_eu(lat, lon):
    """
    Check if the latitude and longitude are within the EU bounding box.
    """
    return eu_lat_min <= lat <= eu_lat_max and eu_lon_min <= lon <= eu_lon_max

def fetch_coordinates_from_table(destination):
    """
    Fetch coordinates and flag for a destination from the DistanceTable in SAP HANA.
    """
    try:
        print(f"Checking DistanceTable for destination: {destination}")
        connection = dbapi.connect(
            address=hana_config['address'],
            port=hana_config['port'],
            user=hana_config['user'],
            password=hana_config['password']
        )
        cursor = connection.cursor()

        query = """
            SELECT "COORDINATES", "FLAG"
            FROM "DWDATA"."DistanceTable"
            WHERE "DESTINATION" = ?;
        """
        cursor.execute(query, (destination,))
        row = cursor.fetchone()
        if row:
            print(f"Found coordinates in DistanceTable for {destination}: {row}")
            return row[0], row[1]  # Return coordinates and flag
        return None, None
    except Exception as e:
        print(f"Error querying DistanceTable for {destination}: {e}")
        return None, None
    finally:
        if connection:
            connection.close()

def fetch_coordinates_with_fallback(address, postal_code, country_key):
    """
    Fetch the latitude and longitude of an address using the Google Geocoding API.
    If the address fails, fall back to postal code and country.
    """
    try:
        print(f"Geocoding address: {address}")
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={requests.utils.quote(address)}&key={api_key}"
        response = requests.get(url)
        if response.status_code == 200:
            result = response.json()
            if result["status"] == "OK":
                location = result["results"][0]["geometry"]["location"]
                return location["lat"], location["lng"]
            else:
                print(f"Geocoding failed for address '{address}': {result['status']}. Falling back to postal code and country.")
        
        fallback_query = f"{postal_code}, {country_key}"
        print(f"Geocoding fallback: {fallback_query}")
        fallback_url = f"https://maps.googleapis.com/maps/api/geocode/json?address={requests.utils.quote(fallback_query)}&key={api_key}"
        fallback_response = requests.get(fallback_url)
        if fallback_response.status_code == 200:
            fallback_result = fallback_response.json()
            if fallback_result["status"] == "OK":
                fallback_location = fallback_result["results"][0]["geometry"]["location"]
                return fallback_location["lat"], fallback_location["lng"]
            else:
                print(f"Geocoding fallback failed for '{fallback_query}': {fallback_result['status']}")
        else:
            print(f"HTTP error {fallback_response.status_code} for fallback query '{fallback_query}'")
    except Exception as e:
        print(f"Error during geocoding for address '{address}': {e}")
    return None, None

def normalize_address(street, postal_code, country_key):
    """
    Normalize address fields by converting to lowercase and stripping spaces.
    """
    if street:
        street = street.strip().lower()
    if postal_code:
        postal_code = postal_code.strip().lower()
    if country_key:
        country_key = country_key.strip().lower()
    return street, postal_code, country_key

def fetch_hana_data():
    """
    Fetch data from SAP HANA and process to include geocoding and EU flagging.
    """
    print("Script started...")
    try:
        print("Attempting to connect to SAP HANA...")
        connection = dbapi.connect(
            address=hostprodhana,
            port=portprodhana,
            user=userprodhana,
            password=passwordprodhana
        )
        print("Successfully connected to SAP HANA!")
    except Exception as e:
        print("Connection to SAP HANA failed:", e)
        return []

    print("Preparing query...")
    query = """
        SELECT  
            "0SHIP_TO_KEY", 
            "0SHIP_POINT_KEY", 
            "4ZSD_PARTN_PARVW", 
            "0ADDR_NUMBR__ZCOUNTRY", 
            "0ADDR_NUMBR__ZPOST_CD", 
            "0ADDR_NUMBR__ZSTREET",
            MAX("Shipping_Point_Lat") AS "Shipping_Point_Lat", 
            MAX("Shipping_Point_Lon") AS "Shipping_Point_Lon"
        FROM "_SYS_BIC"."ZSTIGA.SD.LGSTC/ZSD_GRAVITY"
        GROUP BY 
            "0SHIP_TO_KEY", 
            "0SHIP_POINT_KEY", 
            "4ZSD_PARTN_PARVW", 
            "0ADDR_NUMBR__ZCOUNTRY", 
            "0ADDR_NUMBR__ZPOST_CD",
            "0ADDR_NUMBR__ZSTREET";
    """

    try:
        print("Executing query...")
        cursor = connection.cursor()
        cursor.execute(query)
        results = cursor.fetchall()
        print(f"Query executed successfully. Retrieved {len(results)} rows.")
    except Exception as e:
        print("Query execution failed:", e)
        return []

    cursor.close()
    connection.close()
    print("Connection closed.")

    print("Processing results...")
    processed_set = set()
    processed_results = []
    coordinate_cache = {}

    for row in results:
        shipping_point = row[1]
        shipto = row[0]
        shipping_point_lat = row[6]
        shipping_point_lon = row[7]
        partner_function = row[2]
        country_key = row[3]
        postal_code = row[4]
        street = row[5]

        street, postal_code, country_key = normalize_address(street, postal_code, country_key)

        if street and postal_code and country_key:
            destination = f"{street}, {postal_code}, {country_key}"
        else:
            print(f"Missing address details for ShipTo: {shipto}. Skipping...")
            continue

        if shipping_point_lat and shipping_point_lon:
            origin = f"{shipping_point_lat},{shipping_point_lon}"
        else:
            print(f"Invalid coordinates for Shipping Point: {shipping_point}. Skipping...")
            continue

        # Check the DistanceTable for coordinates
        if destination in coordinate_cache:
            lat, lon, flag = coordinate_cache[destination]
        else:
            coordinates, flag = fetch_coordinates_from_table(destination)
            if coordinates:
                lat, lon = map(float, coordinates.split(","))
            else:
                lat, lon = fetch_coordinates_with_fallback(destination, postal_code, country_key)
                flag = "IN EU" if lat and lon and is_in_eu(lat, lon) else "OUTSIDE EU"
                coordinate_cache[destination] = (lat, lon, flag)

        if lat is None or lon is None:
            print(f"Failed to retrieve coordinates for {destination}. Skipping...")
            continue

        unique_key = (shipping_point, shipto, origin, destination)
        if unique_key not in processed_set:
            processed_set.add(unique_key)
            processed_results.append({
                "shipping_point": shipping_point,
                "shipto": shipto,
                "origin": origin,
                "destination": destination,
                "coordinates": f"{lat},{lon}",
                "flag": flag
            })
        else:
            print(f"Duplicate found for Shipping Point: {shipping_point}, ShipTo: {shipto}. Skipping...")

    print(f"Processed {len(processed_results)} rows.")
    return processed_results

def export_to_excel(data, filename):
    """
    Export data to an Excel file, removing duplicates.
    """
    try:
        print(f"Exporting data to {filename}...")
        df = pd.DataFrame(data)
        df.drop_duplicates(inplace=True)
        df.to_excel(filename, index=False, engine='openpyxl')
        print(f"Data successfully exported to {filename}.")
    except Exception as e:
        print(f"Failed to export data to Excel: {e}")

if __name__ == "__main__":
    print("Starting main script...")
    processed_results = fetch_hana_data()
    if processed_results:
        export_to_excel(processed_results, "Processed_Data.xlsx")
    else:
        print("No data to process or export.")
