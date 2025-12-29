#dataframe to read csv file and return dataframe
def read_csv_to_dataframe(file_path):
    import pandas as pd
    df = pd.read_csv(file_path)
    return df


file_path = "path/to/your/csvfile.csv"
dataframe = read_csv_to_dataframe(file_path)