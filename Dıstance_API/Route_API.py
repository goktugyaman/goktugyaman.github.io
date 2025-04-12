'''
Created on 29 Nov 2024

@author: goktug.yaman
'''

# SAP HANA configuration
hana_config = {
 "address": hostprodhana,
 "port": portprodhana,  
 "user": userprodhana,
 "password": passwordprodhana
}
api_key = "XXXX"

from hdbcli import dbapi  # SAP HANA Python Driver
import requests
from Origin_Destination_List import fetch_hana_data

# Function to fetch existing origin-destination pairs from SAP HANA
def get_existing_pairs():
    """
    Fetch existing origin-destination pairs from SAP HANA.
    """
    try:
        print("Fetching existing origin-destination pairs from SAP HANA...")
        connection = dbapi.connect(
            address=hana_config['address'],
            port=hana_config['port'],
            user=hana_config['user'],
            password=hana_config['password']
        )
        cursor = connection.cursor()

        query = """
            SELECT "ORIGIN", "DESTINATION" FROM "DWDATA"."DistanceTable";
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        existing_pairs = set((row[0], row[1]) for row in rows)  # Convert to set for fast lookup

        print(f"Fetched {len(existing_pairs)} existing origin-destination pairs from DistanceTable.")
        return existing_pairs
    except Exception as e:
        print("Failed to fetch existing pairs from SAP HANA:", e)
        return set()
    finally:
        if connection:
            connection.close()

# Function to extract zip code and country from the destination
def extract_zip_country(destination):
    """
    Extract zip code and country from the destination string.
    """
    parts = destination.split(",")
    if len(parts) >= 2:
        return f"{parts[-2].strip()}, {parts[-1].strip()}"
    return destination  # If extraction fails, return original destination

# Function to fetch distance using Google Distance Matrix API
def fetch_distance(origin, destination, api_key, cache):
    """
    Fetch distances using the Google Distance Matrix API, with caching.
    """
    # Check if the result is already in the cache
    if (origin, destination) in cache:
        print(f"Using cached distance for {origin} -> {destination}")
        return cache[(origin, destination)]

    try:
        print(f"Fetching distance from {origin} to {destination}")
        url = f"https://maps.googleapis.com/maps/api/distancematrix/json?origins={origin}&destinations={destination}&mode=driving&key={api_key}"
        response = requests.get(url)
        
        if response.status_code == 200:
            result = response.json()
            if 'rows' in result and result['rows'][0]['elements'][0]['status'] == 'OK':
                distance_meters = result['rows'][0]['elements'][0]['distance']['value']
                distance_km = round(distance_meters / 1000, 2)
                # Cache the result
                cache[(origin, destination)] = distance_km
                return distance_km
        else:
            print(f"HTTP error {response.status_code} for {origin} -> {destination}")
    except Exception as e:
        print(f"Error fetching distance for {origin} -> {destination}: {e}")
    # Cache the result as None to avoid repeated calls for failed cases
    cache[(origin, destination)] = None
    return None

# Function to insert new distances, coordinates, and flags into SAP HANA
def insert_new_data(data):
    """
    Insert new records (distance, coordinates, and flag) into DistanceTable.
    """
    try:
        print("Inserting new records into SAP HANA...")
        connection = dbapi.connect(
            address=hana_config['address'],
            port=hana_config['port'],
            user=hana_config['user'],
            password=hana_config['password']
        )
        cursor = connection.cursor()

        for shipping_point, shipto, origin, destination, distance_km, coordinates, flag in data:
            insert_query = """
                INSERT INTO "DWDATA"."DistanceTable" (
                    "SHIPPING_POINT", "SHIPTO", "ORIGIN", "DESTINATION", 
                    "DISTANCE_KM", "COORDINATES", "FLAG"
                )
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """
            cursor.execute(insert_query, (
                shipping_point, shipto, origin, destination, 
                distance_km if distance_km is not None else None, 
                coordinates, flag
            ))

        connection.commit()
        print(f"Successfully inserted {len(data)} new records into DistanceTable.")
    except Exception as e:
        print("Failed to insert new data into SAP HANA:", e)
    finally:
        if connection:
            connection.close()
            print("SAP HANA connection closed.")

# Main function
if __name__ == "__main__":
    
    # Fetch all origin-destination pairs from Origin_Destination_List
    origin_dest_pairs = fetch_hana_data()
    
    if origin_dest_pairs:
        existing_pairs = get_existing_pairs()
        
        # Filter out existing pairs to avoid duplicate API calls
        new_pairs = [
            (pair["shipping_point"], pair["shipto"], pair["origin"], pair["destination"], pair["coordinates"], pair["flag"]) 
            for pair in origin_dest_pairs 
            if (pair["origin"], pair["destination"]) not in existing_pairs
        ]
        
        print(f"{len(new_pairs)} new pairs to process out of {len(origin_dest_pairs)} total pairs.")
        
        if new_pairs:
            new_data = []
            cache = {}  # Cache to store API responses
            
            for shipping_point, shipto, origin, destination, coordinates, flag in new_pairs:
                # Use only postal code and country if not in EU
                if flag != "IN EU":
                    destination = extract_zip_country(destination)
                
                # Fetch the distance
                distance_km = fetch_distance(origin, destination, api_key, cache)
                
                # Add to new data to insert into the database
                new_data.append((shipping_point, shipto, origin, destination, distance_km, coordinates, flag))
            
            if new_data:
                insert_new_data(new_data)
            else:
                print("No new records to insert.")
        else:
            print("No new pairs to process.")
    else:
        print("No origin-destination pairs to process.")
