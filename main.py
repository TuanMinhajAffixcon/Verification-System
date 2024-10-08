import textdistance
import pandas as pd
from template import conn
from utils import *
import snowflake.connector
from pydantic import BaseModel
import uvicorn
from fuzzywuzzy import fuzz
from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, HTTPException, status
import io
from datetime import datetime
from fastapi.security import HTTPBasic, HTTPBasicCredentials

app = FastAPI()

# Simple basic authentication
security = HTTPBasic()

# Hardcoded test user
test_user = {
    "username": "testuser",
    "password": "affixcon1234"
}

def verify_credentials(credentials: HTTPBasicCredentials):
    if credentials.username == test_user["username"] and credentials.password == test_user["password"]:
        return {"username": credentials.username}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Basic"},
    )

class UserData(BaseModel):
    # country_prefix: str
    first_name: str
    middle_name: str
    sur_name: str
    dob: str
    address_line1: str
    suburb: str
    state: str
    postcode: str
    mobile: str
    email: str


# def verify_function(data: UserData):

@app.post("/verify_user/")
def verify_user(data: UserData, credentials: HTTPBasicCredentials = Depends(security)):
    verify_credentials(credentials)
    
    # data = UserData(**data)
    # if data["country_prefix"] == 'au':
    #     table = "AU_RESIDENTIAL"
    # elif data["country_prefix"] == 'nz':
    #     table = "NZ_RESIDENTIAL"

    try:
        cursor = conn.cursor()
        query = f"""
            WITH InputData AS (
                SELECT
                    '{data.first_name}' AS first_name_input,
                    '{data.middle_name}' AS middle_name_input,
                    '{data.sur_name}' AS sur_name_input,
                    '{data.dob}' AS dob_input
            )
            SELECT
                First_name, middle_name, sur_name, dob, ad1, suburb, state, postcode, PHONE2_MOBILE, EMAILADDRESS
            FROM
                DATA_VERIFICATION.PUBLIC.AU_RESIDENTIAL AS resident,
                InputData AS input
            WHERE
                (
                    (LOWER(input.sur_name_input) IS NOT NULL AND LOWER(input.sur_name_input) != '' AND LOWER(resident.sur_name) LIKE LOWER(input.sur_name_input))
                    OR (LOWER(input.middle_name_input) IS NOT NULL AND LOWER(input.middle_name_input) != '' AND LOWER(resident.middle_name) = LOWER(input.middle_name_input))
                    OR (LOWER(input.first_name_input) IS NOT NULL AND LOWER(input.first_name_input) != '' AND LOWER(resident.first_name) = LOWER(input.first_name_input))
                    AND (input.dob_input IS NOT NULL AND input.dob_input != '' AND resident.DOB = input.dob_input)
                )
            LIMIT 1
        """

        cursor.execute(query)
        df = cursor.fetch_pandas_all()



        if df.empty:
            raise HTTPException(status_code=404, detail="No match found")


        # Your existing logic starts here
        fields = [
            ('FIRST_NAME', data.first_name, 0),
            ('MIDDLE_NAME', data.middle_name, 1),
            ('SUR_NAME', data.sur_name, 2)
        ]

        def update_name_str(row):
            name_Str = "XXX"
            for db_column, input_field, str_index in fields:
                name_Str = apply_name_matching(row, name_Str, db_column, input_field, str_index)
            return name_Str

        # name_match_str = df.apply(update_name_str, axis=1)[0]

        df['name_match_str'] = df.apply(update_name_str, axis=1)
        first_name_similarity = max(textdistance.jaro_winkler(df.FIRST_NAME[0].lower(), data.first_name.lower()) * 100, 0) if textdistance.jaro_winkler(df.FIRST_NAME[0].lower(), data.first_name.lower()) * 100 > 65 else 0
        middle_name_similarity = max(textdistance.jaro_winkler(df.MIDDLE_NAME[0].lower(), data.middle_name.lower()) * 100, 0) if textdistance.jaro_winkler(df.MIDDLE_NAME[0].lower(), data.middle_name.lower()) * 100 > 65 else 0
        sur_name_similarity = max(textdistance.jaro_winkler(df.SUR_NAME[0].lower(), data.sur_name.lower()) * 100, 0) if textdistance.jaro_winkler(df.SUR_NAME[0].lower(), data.sur_name.lower()) * 100 > 65 else 0

        if df['name_match_str'][0][0] == 'T':
            first_name_similarity = 100
        if df['name_match_str'][0][1] == 'T':
            middle_name_similarity = 100
        if df['name_match_str'][0][2] == 'T':
            sur_name_similarity = 100

        full_name_request = (data.first_name.strip() + " " + data.middle_name.strip() + " "+ data.sur_name.strip()).strip().lower()
        full_name_matched = (df.FIRST_NAME[0].strip()+ " "+df.MIDDLE_NAME[0].strip()+ " "+df.SUR_NAME[0].strip()).lower()
        name_obj = Name(full_name_request)
        
        # # Apply the different matching methods from the Name class
        match_results = {
            "Exact Match": (df['name_match_str'] == 'EEE').any(),
            "Hyphenated Match": name_obj.hyphenated(full_name_matched),
            "Transposed Match": name_obj.transposed(full_name_matched),
            "Middle Name Mismatch": df['name_match_str'].str.contains('E.*E$', regex=True).any(),
            "Initial Match": name_obj.initial(full_name_matched),
            "SurName only Match": df['name_match_str'].str.contains('^[ETMD].*E$', regex=True).any(),
            "Fuzzy Match": name_obj.fuzzy(full_name_matched),
            "Nickname Match": name_obj.nickname(full_name_matched),
            "Missing Part Match": name_obj.missing(full_name_matched),
            "Different Name": name_obj.different(full_name_matched)
        }
        
        # # Filter out any matches that returned False
        match_results = {k: v for k, v in match_results.items() if v}
        top_match = next(iter(match_results.items()), ("No Match Found", ""))

        df['Name_Match_Level'] = top_match[0]
        
        full_name_similarity = max(textdistance.jaro_winkler(full_name_request,full_name_matched) * 100, 0) if textdistance.jaro_winkler(full_name_request,full_name_matched) * 100 > 65 else 0

        # full_name_similarity = (textdistance.jaro_winkler(full_name_request,full_name_matched)*100) 
        # df['full_name_similarity'] = df['full_name_similarity'].apply(lambda score: int(score) if score > 65 else 0)
        if fuzz.token_sort_ratio(full_name_request,full_name_matched)==100 and top_match[0] !='Exact Match':
            full_name_similarity = 100
            df['Name_Match_Level'] = 'Transposed Match'
        
        df['dob_match'] = df['DOB'].apply(lambda x: Dob(data.dob).exact(x))
        address_str = "XXXXXX"

        source = {
            # 'Gnaf_Pid': address_id,
            'Ad1': df["AD1"][0],
            'Suburb': df["SUBURB"][0],
            'State': df["STATE"][0],
            'Postcode': str(df["POSTCODE"][0])
        }
        source_output = address_parsing(df['AD1'][0])
        source = {**source, **source_output}
        # # # st.write(source)


        parsed_address = {
            # 'Gnaf_Pid': address_id,
            'Ad1': data.address_line1,
            'Suburb': data.suburb,
            'State': data.state,
            'Postcode': str(data.postcode)
        }
        parsed_output = address_parsing(data.address_line1)
        parsed_address = {**parsed_address, **parsed_output}
        # # # st.write(parsed_address)

        address_checker = Address(parsed_address=parsed_address,source_address=source)
        address_str=address_checker.address_line1_match(address_str)
        df['Address_Matching_String'] = address_str

        address_line_similarity = max(textdistance.jaro_winkler(df.AD1[0].lower(),data.address_line1.lower()) * 100, 0) if textdistance.jaro_winkler(df.AD1[0].lower(),data.address_line1[0].lower()) * 100 > 65 else 0
        weight1 = 40 if 90<=address_line_similarity <=100 else 30 if 85<=address_line_similarity <90 else 0 
        
        suburb_similarity = max(textdistance.jaro_winkler(df.SUBURB[0].lower(),data.suburb.lower()) * 100, 0) if textdistance.jaro_winkler(df.SUBURB[0].lower(),data.suburb[0].lower()) * 100 > 65 else 0
        weight2 = 30 if 90<=suburb_similarity <=100 else 25 if 85<=suburb_similarity <90 else 0 
        
        state_similarity = max(textdistance.jaro_winkler(df.STATE[0].lower(),data.state.lower()) * 100, 0) if textdistance.jaro_winkler(df.STATE[0].lower(),data.state[0].lower()) * 100 > 65 else 0
        weight3 = 10 if 90<=state_similarity <=100 else  0

        postcde_similarity = max(textdistance.jaro_winkler(str(df.POSTCODE[0]),str(data.postcode)) * 100, 0)  if textdistance.jaro_winkler(str(df.POSTCODE[0]),str(data.postcode)) * 100 == 100 else 0
        weight4 = 20 if postcde_similarity ==100 else 0 
        
        total_weight = weight1+weight2+weight3+weight4
        if total_weight > 90:
            match_level = f'Full Match, {total_weight}'
        elif 80 <= total_weight <= 90:
            match_level = f'Partial Match, {total_weight}'
        else:
            match_level = 'No Match'
        df['Address_Match_Level'] = match_level

        matching_levels = get_matching_level(df,data.dob,data.mobile,data.email,full_name_similarity,total_weight)
        Overall_Matching_Level = ', '.join(matching_levels)
        Overall_Verified_Level = append_based_on_verification(Overall_Matching_Level,verified_by=True)

        # # st.write("source",source)
        # # st.write("parsed_address",parsed_address)
        # # st.write("address_str",address_str)
        # df_transposed = df.T
        # df_transposed.columns = ['Results']

        # return {
        #     "name_match_str":df.name_match_str[0],
        #     "first_name_similarity":first_name_similarity,
        #     "middle_name_similarity":middle_name_similarity,
        #     "sur_name_similarity":sur_name_similarity

        # }

        return {
            'FIRST_NAME':df.FIRST_NAME[0],            
            'MIDDLE_NAME':df.MIDDLE_NAME[0],             
            'SUR_NAME':df.SUR_NAME[0],          
            'DOB':str(df.DOB[0]),
            'AD1':df.AD1[0],           
            "SUBURB":df.SUBURB[0],
            'STATE':df.STATE[0],
            'POSTCODE':str(df.POSTCODE[0]),
            'PHONE2_MOBILE':str(df.PHONE2_MOBILE[0]),
            'EMAILADDRESS':df.EMAILADDRESS[0],
            "name_match_str":df.name_match_str[0],          
            "first_name_similarity":"{}%".format(int(first_name_similarity)),           
            "middle_name_similarity":"{}%".format(int(middle_name_similarity)),          
            "sur_name_similarity":"{}%".format(int(sur_name_similarity)),
            "Name Match Level": df.Name_Match_Level[0],
            "full_name_similarity":  "{}%".format(int(full_name_similarity)),
            "dob_match": df['dob_match'][0],
            "Address Matching String" : df.Address_Matching_String[0],
            "address_line_similarity"  : "{}%".format(int(address_line_similarity)),
            "suburb_similarity"  : "{}%".format(int(suburb_similarity)),
            "state_similarity"  :  "{}%".format(int(state_similarity)),
            "postcde_similarity" : "{}%".format(int(postcde_similarity)),
            "Address_Match_Level": df.Address_Match_Level[0],
            "Overall Matching Level"  : Overall_Matching_Level,
            "Overall Verified Level "  : Overall_Verified_Level

        }
    except snowflake.connector.errors.ProgrammingError as e:
        raise HTTPException(status_code=500, detail=f"Error executing query: {e}")

    finally:
        cursor.close()


@app.post("/batch_process/")
async def batch_process(file: UploadFile = File(...), credentials: HTTPBasicCredentials = Depends(security)):
    verify_credentials(credentials)
    try:
        # Read CSV file as pandas DataFrame
        contents = await file.read()
        df_users = pd.read_csv(io.StringIO(contents.decode("utf-8"))).fillna("")
        df_users['dob'] = pd.to_datetime(df_users['dob'])
        df_users['dob'] = df_users['dob'].dt.strftime('%Y-%m-%d').fillna("")

        results = []
        cursor = conn.cursor()

        # Loop through each user record in the CSV
        for index, row in df_users.iterrows():
            query = f"""
                WITH InputData AS (
                    SELECT
                        '{row['first_name']}' AS first_name_input,
                        '{row['middle_name']}' AS middle_name_input,
                        '{row['sur_name']}' AS sur_name_input,
                        '{row['dob']}' AS dob_input
                )
                SELECT
                    First_name, middle_name, sur_name, dob, ad1, suburb, state, postcode, PHONE2_MOBILE, EMAILADDRESS
                FROM
                    DATA_VERIFICATION.PUBLIC.AU_RESIDENTIAL AS resident,
                    InputData AS input
                WHERE
                    (
                        (LOWER(input.sur_name_input) IS NOT NULL AND LOWER(input.sur_name_input) != '' AND LOWER(resident.sur_name) LIKE LOWER(input.sur_name_input))
                        OR (LOWER(input.middle_name_input) IS NOT NULL AND LOWER(input.middle_name_input) != '' AND LOWER(resident.middle_name) = LOWER(input.middle_name_input))
                        OR (LOWER(input.first_name_input) IS NOT NULL AND LOWER(input.first_name_input) != '' AND LOWER(resident.first_name) = LOWER(input.first_name_input))
                        AND (input.dob_input IS NOT NULL AND input.dob_input != '' AND resident.DOB = input.dob_input)
                    )
                LIMIT 1
            """
            cursor.execute(query)
            df = cursor.fetch_pandas_all()

            if df.empty:
                results.append({"index": index, "result": "No match found"})
            else:
                # results.append({"index": index, "result": df_result.to_dict(orient="records")})
        # Your existing logic starts here
                fields = [
                    ('FIRST_NAME', row['first_name'], 0),
                    ('MIDDLE_NAME', row['middle_name'], 1),
                    ('SUR_NAME', row['sur_name'], 2)
                ]

                def update_name_str(row):
                    name_Str = "XXX"
                    for db_column, input_field, str_index in fields:
                        name_Str = apply_name_matching(row, name_Str, db_column, input_field, str_index)
                    return name_Str

                # name_match_str = df.apply(update_name_str, axis=1)[0]

                df['name_match_str'] = df.apply(update_name_str, axis=1)
                first_name_similarity = max(textdistance.jaro_winkler(df.FIRST_NAME[0].lower(), row['first_name'].lower()) * 100, 0) if textdistance.jaro_winkler(df.FIRST_NAME[0].lower(), row['first_name'].lower()) * 100 > 65 else 0
                middle_name_similarity = max(textdistance.jaro_winkler(df.MIDDLE_NAME[0].lower(), row['middle_name'].lower()) * 100, 0) if textdistance.jaro_winkler(df.MIDDLE_NAME[0].lower(), row['middle_name'].lower()) * 100 > 65 else 0
                sur_name_similarity = max(textdistance.jaro_winkler(df.SUR_NAME[0].lower(), row['sur_name'].lower()) * 100, 0) if textdistance.jaro_winkler(df.SUR_NAME[0].lower(), row['sur_name'].lower()) * 100 > 65 else 0

                if df['name_match_str'][0][0] == 'T':
                    first_name_similarity = 100
                if df['name_match_str'][0][1] == 'T':
                    middle_name_similarity = 100
                if df['name_match_str'][0][2] == 'T':
                    sur_name_similarity = 100

                full_name_request = (row['first_name'].strip() + " " + row['middle_name'].strip() + " "+ row['sur_name'].strip()).strip().lower()
                full_name_matched = (df.FIRST_NAME[0].strip()+ " "+df.MIDDLE_NAME[0].strip()+ " "+df.SUR_NAME[0].strip()).lower()
                name_obj = Name(full_name_request)
                
                # # Apply the different matching methods from the Name class
                match_results = {
                    "Exact Match": (df['name_match_str'] == 'EEE').any(),
                    "Hyphenated Match": name_obj.hyphenated(full_name_matched),
                    "Transposed Match": name_obj.transposed(full_name_matched),
                    "Middle Name Mismatch": df['name_match_str'].str.contains('E.*E$', regex=True).any(),
                    "Initial Match": name_obj.initial(full_name_matched),
                    "SurName only Match": df['name_match_str'].str.contains('^[ETMD].*E$', regex=True).any(),
                    "Fuzzy Match": name_obj.fuzzy(full_name_matched),
                    "Nickname Match": name_obj.nickname(full_name_matched),
                    "Missing Part Match": name_obj.missing(full_name_matched),
                    "Different Name": name_obj.different(full_name_matched)
                }
                
                # # Filter out any matches that returned False
                match_results = {k: v for k, v in match_results.items() if v}
                top_match = next(iter(match_results.items()), ("No Match Found", ""))

                df['Name_Match_Level'] = top_match[0]
                
                full_name_similarity = max(textdistance.jaro_winkler(full_name_request,full_name_matched) * 100, 0) if textdistance.jaro_winkler(full_name_request,full_name_matched) * 100 > 65 else 0

                # full_name_similarity = (textdistance.jaro_winkler(full_name_request,full_name_matched)*100) 
                # df['full_name_similarity'] = df['full_name_similarity'].apply(lambda score: int(score) if score > 65 else 0)
                if fuzz.token_sort_ratio(full_name_request,full_name_matched)==100 and top_match[0] !='Exact Match':
                    full_name_similarity = 100
                    df['Name_Match_Level'] = 'Transposed Match'
                
                df['dob_match'] = df['DOB'].apply(lambda x: Dob(str(row['dob'])).exact(x))
                # datetime.strptime(str(row['dob']), "%m/%d/%Y").strftime("%Y-%m-%d")
                # df['dob_match'] = Dob(str(row['dob'])).exact(str(df.DOB)[0])
                # df['dob_match'] = Dob(datetime.strptime(str(row['dob']), "%m/%d/%Y").strftime("%Y-%m-%d")).exact(str(df.DOB)[0])
                # df['dob_match'] = df.apply(lambda row: Dob(datetime.strptime(str(row['dob']), "%m/%d/%Y").strftime("%Y-%m-%d")).exact(str(df.DOB)[0]), axis=1)
                # df['dob_match'] = df.apply(lambda row: Dob(str(row['dob']).exact(str(df.DOB))), axis=1)
                
                # df['dob_match'] = df.apply(compare_dob, axis=1,df=df)

                address_str = "XXXXXX"

                source = {
                    # 'Gnaf_Pid': address_id,
                    'Ad1': df["AD1"][0],
                    'Suburb': df["SUBURB"][0],
                    'State': df["STATE"][0],
                    'Postcode': str(df["POSTCODE"][0])
                }
                source_output = address_parsing(df['AD1'][0])
                source = {**source, **source_output}
                # # # st.write(source)


                parsed_address = {
                    # 'Gnaf_Pid': address_id,
                    'Ad1': row['address_line1'],
                    'Suburb': row['suburb'],
                    'State': row['state'],
                    'Postcode': str(row['postcode'])
                }
                parsed_output = address_parsing(row['address_line1'])
                parsed_address = {**parsed_address, **parsed_output}
                # # # st.write(parsed_address)

                address_checker = Address(parsed_address=parsed_address,source_address=source)
                address_str=address_checker.address_line1_match(address_str)
                df['Address_Matching_String'] = address_str

                address_line_similarity = max(textdistance.jaro_winkler(df.AD1[0].lower(),row['address_line1'].lower()) * 100, 0) if textdistance.jaro_winkler(df.AD1[0].lower(),row['address_line1'].lower()) * 100 > 65 else 0
                weight1 = 40 if 90<=address_line_similarity <=100 else 30 if 85<=address_line_similarity <90 else 0 
                
                suburb_similarity = max(textdistance.jaro_winkler(df.SUBURB[0].lower(),row['suburb'].lower()) * 100, 0) if textdistance.jaro_winkler(df.SUBURB[0].lower(),row['suburb'].lower()) * 100 > 65 else 0
                weight2 = 30 if 90<=suburb_similarity <=100 else 25 if 85<=suburb_similarity <90 else 0 
                
                state_similarity = max(textdistance.jaro_winkler(df.STATE[0].lower(),row['state'].lower()) * 100, 0) if textdistance.jaro_winkler(df.STATE[0].lower(),row['state'].lower()) * 100 > 65 else 0
                weight3 = 10 if 90<=state_similarity <=100 else  0

                postcde_similarity = max(textdistance.jaro_winkler(str(df.POSTCODE[0]),str(row['postcode'])) * 100, 0)  if textdistance.jaro_winkler(str(df.POSTCODE[0]),str(row['postcode'])) * 100 == 100 else 0
                weight4 = 20 if postcde_similarity ==100 else 0 
                
                total_weight = weight1+weight2+weight3+weight4
                if total_weight > 90:
                    match_level = f'Full Match, {total_weight}'
                elif 80 <= total_weight <= 90:
                    match_level = f'Partial Match, {total_weight}'
                else:
                    match_level = 'No Match'
                df['Address_Match_Level'] = match_level

                matching_levels = get_matching_level(df,row['dob'],row['mobile'],row['email'],full_name_similarity,total_weight)
                Overall_Matching_Level = ', '.join(matching_levels)
                Overall_Verified_Level = append_based_on_verification(Overall_Matching_Level,verified_by=True)

                # # st.write("source",source)
                # # st.write("parsed_address",parsed_address)
                # # st.write("address_str",address_str)
                # df_transposed = df.T
                # df_transposed.columns = ['Results']

                # return {
                #     "name_match_str":df.name_match_str[0],
                #     "first_name_similarity":first_name_similarity,
                #     "middle_name_similarity":middle_name_similarity,
                #     "sur_name_similarity":sur_name_similarity

                # }
                df_result =  {
                    'FIRST_NAME':df.FIRST_NAME[0],            
                    'MIDDLE_NAME':df.MIDDLE_NAME[0],             
                    'SUR_NAME':df.SUR_NAME[0],          
                    'DOB':str(df.DOB[0]),
                    'AD1':df.AD1[0],           
                    "SUBURB":df.SUBURB[0],
                    'STATE':df.STATE[0],
                    'POSTCODE':str(df.POSTCODE[0]),
                    'PHONE2_MOBILE':str(df.PHONE2_MOBILE[0]),
                    'EMAILADDRESS':df.EMAILADDRESS[0],
                    "name_match_str":df.name_match_str[0],          
                    "first_name_similarity":"{}%".format(int(first_name_similarity)),           
                    "middle_name_similarity":"{}%".format(int(middle_name_similarity)),          
                    "sur_name_similarity":"{}%".format(int(sur_name_similarity)),
                    "Name Match Level": df.Name_Match_Level[0],
                    "full_name_similarity":  "{}%".format(int(full_name_similarity)),
                    "dob_match": df['dob_match'][0],
                    # 'test_dob': str(row['dob']),
                    "Address Matching String" : df.Address_Matching_String[0],
                    "address_line_similarity"  : "{}%".format(int(address_line_similarity)),
                    "suburb_similarity"  : "{}%".format(int(suburb_similarity)),
                    "state_similarity"  :  "{}%".format(int(state_similarity)),
                    "postcde_similarity" : "{}%".format(int(postcde_similarity)),
                    "Address_Match_Level": df.Address_Match_Level[0],
                    "Overall Matching Level"  : Overall_Matching_Level,
                    "Overall Verified Level "  : Overall_Verified_Level

                }
            
                results.append({"index": index, "result": df_result})


        return {"results": results}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# @app.get("/")
# def read_root():
#     return {"message": "Welcome to the Data Verification API"}

@app.get("/")
def read_root(credentials: HTTPBasicCredentials = Depends(security)):
    user = verify_credentials(credentials)
    return {"message": "Welcome to the Data Verification API"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)

