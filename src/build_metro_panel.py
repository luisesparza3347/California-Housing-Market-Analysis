# This program builds a metro-level cross-section for four California CBSAs in
# 2024. It reconciles CPI, building permits, population, mortgage rates, and
# unemployment onto a shared CBSA-month grid and outputs one table for Power BI.
#
# This is not a regression dataset. HPI, Mortgage_30yr, and Unemployment_Rate
# are statewide or national series keyed on Date only, so across 4 CBSAs x 6
# dates they carry no cross-metro variation at all. Only CPI genuinely varies
# by metro, and regressing HPI on CPI here returns a null coefficient at
# p = 0.97: 24 rows are effectively 6 time points repeated four times, not 24
# independent observations. The output stays because the wide CBSA-month grid
# is what the Power BI dashboard needs, not because it supports a regression.
# See the README for the full account of this diagnosis.

import unicodedata
import numpy as np
import pandas as pd

# Six bi-monthly periods, set by the CPI reporting schedule
DATES_2024 = ['2024-02', '2024-04', '2024-06', '2024-08', '2024-10', '2024-12']

RELEVANT_CBSAS = [
    'Los Angeles-Long Beach-Anaheim, CA',
    'San Diego-Chula Vista-Carlsbad, CA',
    'San Francisco-Oakland-Fremont, CA',
    'Riverside-San Bernardino-Ontario, CA',
]

MONTH_MAP = {
    'Jan': '2024-01', 'Feb': '2024-02', 'Mar': '2024-03', 'Apr': '2024-04',
    'May': '2024-05', 'Jun': '2024-06', 'Jul': '2024-07', 'Aug': '2024-08',
    'Sep': '2024-09', 'Oct': '2024-10', 'Nov': '2024-11', 'Dec': '2024-12',
}

# ---- Load source files ----

population = pd.read_csv("data/California_Population_and_Population_Density.csv", encoding="latin-1")
hpi = pd.read_csv("data/California_HPI_Index_filtered_data_set .csv")
cpi = pd.read_csv("data/California_CPI_2024_Major-Counties.csv")
mortgage = pd.read_csv("data/US_Fixed_Rate_Mortage_Weekly_2024.csv")
income = pd.read_csv("data/Median_Household_Income_2024.csv")
employment = pd.read_csv("data/Employment_Data_California_2024.csv")
permits = pd.read_csv("data/California_housing_permits_2024_CBSA.csv")

permits.columns = permits.columns.str.strip()

population['Population_Estimate_July1st_2024'] = (
    population['Population_Estimate_July1st_2024']
    .astype(str).str.replace(',', '', regex=False).astype(float)
)

# ---- Mortgage: weekly to monthly ----

mortgage['Week'] = pd.to_datetime(mortgage['Week'])
mortgage['Month'] = mortgage['Week'].dt.to_period('M')
mortgage_monthly = (
    mortgage.groupby('Month')[['U.S._30_Year_FRM', 'U.S._15_Year_FRM']]
    .mean().reset_index()
)
mortgage_monthly['Month'] = mortgage_monthly['Month'].astype(str)
mortgage_std = (
    mortgage_monthly[mortgage_monthly['Month'].isin(DATES_2024)]
    .rename(columns={
        'Month': 'Date',
        'U.S._30_Year_FRM': 'Mortgage_30yr',
        'U.S._15_Year_FRM': 'Mortgage_15yr',
    })
)

# ---- Unemployment: monthly to the six CPI periods ----

employment['Date'] = employment['Month'].map(MONTH_MAP)
unemp_std = employment[employment['Date'].isin(DATES_2024)]

# ---- HPI: quarterly to the six CPI periods ----

hpi_2024 = hpi[hpi['yr'] == 2024].rename(columns={'index_nsa': 'HPI'}).copy()
hpi_2024['period'] = hpi_2024['period'].astype(int)

quarter_to_months = {
    1: ['2024-02'], 2: ['2024-04', '2024-06'],
    3: ['2024-08'], 4: ['2024-10', '2024-12'],
}

hpi_standardized = pd.DataFrame([
    {'Date': month, 'HPI': row['HPI']}
    for _, row in hpi_2024.iterrows()
    for month in quarter_to_months.get(row['period'], [])
])

# Statewide and national series, keyed on Date only
macro = (
    mortgage_std
    .merge(unemp_std[['Date', 'Unemployment_Rate']], on='Date', how='left')
    .merge(hpi_standardized, on='Date', how='left')
)

# ---- CPI: bi-monthly metro series onto the shared grid ----

cpi_clean = cpi[cpi['Month'] != 'Annual Average'].copy()
cpi_clean['Month_key'] = cpi_clean['Month'].astype(str).str.strip().str[:3]
cpi_clean['Date'] = cpi_clean['Month_key'].map(MONTH_MAP)

# San Diego and Riverside report on the off month, so shift them forward one row
cpi_clean['San_Diego-Carlsbad'] = cpi_clean['San_Diego-Carlsbad'].shift(1)
cpi_clean['Riverside-San_Bernardino-Ontario'] = (
    cpi_clean['Riverside-San_Bernardino-Ontario'].shift(1)
)

cpi_long = (
    cpi_clean[cpi_clean['Date'].isin(DATES_2024)]
    .melt(
        id_vars=['Date'],
        value_vars=[
            'Los_Angeles-Long_Beach-Anaheim',
            'San_Francisco-Oakland-Hayward',
            'San_Diego-Carlsbad',
            'Riverside-San_Bernardino-Ontario',
        ],
        var_name='Region',
        value_name='CPI',
    )
)

region_to_cbsa = {
    'Los_Angeles-Long_Beach-Anaheim': 'Los Angeles-Long Beach-Anaheim, CA',
    'San_Francisco-Oakland-Hayward': 'San Francisco-Oakland-Fremont, CA',
    'San_Diego-Carlsbad': 'San Diego-Chula Vista-Carlsbad, CA',
    'Riverside-San_Bernardino-Ontario': 'Riverside-San Bernardino-Ontario, CA',
}

cpi_long['CBSA'] = cpi_long['Region'].map(region_to_cbsa)
cpi_cbsa = (
    cpi_long.dropna(subset=['CBSA'])[['CBSA', 'Date', 'CPI']]
    .sort_values(['CBSA', 'Date'])
)
cpi_cbsa['CPI'] = cpi_cbsa.groupby('CBSA')['CPI'].transform(lambda x: x.ffill().bfill())

# ---- Population: cities to CBSAs via county membership ----
# CBSAs are defined as whole counties, so cities are assigned through their
# county rather than listed one by one.

CBSA_COUNTIES = {
    'Los Angeles-Long Beach-Anaheim, CA': ['Los Angeles', 'Orange'],
    'Riverside-San Bernardino-Ontario, CA': ['Riverside', 'San Bernardino'],
    'San Diego-Chula Vista-Carlsbad, CA': ['San Diego'],
    'San Francisco-Oakland-Fremont, CA': [
        'San Francisco', 'San Mateo', 'Alameda', 'Contra Costa', 'Marin'
    ],
}

COUNTY_CITIES = {
    'Los Angeles': [
        'Agoura Hills', 'Alhambra', 'Arcadia', 'Artesia', 'Avalon', 'Azusa',
        'Baldwin Park', 'Bell', 'Bell Gardens', 'Bellflower', 'Beverly Hills',
        'Bradbury', 'Burbank', 'Calabasas', 'Carson', 'Cerritos', 'Claremont',
        'Commerce', 'Compton', 'Covina', 'Cudahy', 'Culver City', 'Diamond Bar',
        'Downey', 'Duarte', 'El Monte', 'El Segundo', 'Gardena', 'Glendale',
        'Glendora', 'Hawaiian Gardens', 'Hawthorne', 'Hermosa Beach',
        'Hidden Hills', 'Huntington Park', 'Industry', 'Inglewood', 'Irwindale',
        'La Canada Flintridge', 'La Habra Heights', 'La Mirada', 'La Puente',
        'La Verne', 'Lakewood', 'Lancaster', 'Lawndale', 'Lomita', 'Long Beach',
        'Los Angeles', 'Lynwood', 'Malibu', 'Manhattan Beach', 'Maywood',
        'Monrovia', 'Montebello', 'Monterey Park', 'Norwalk', 'Palmdale',
        'Palos Verdes Estates', 'Paramount', 'Pasadena', 'Pico Rivera',
        'Pomona', 'Rancho Palos Verdes', 'Redondo Beach', 'Rolling Hills',
        'Rolling Hills Estates', 'Rosemead', 'San Dimas', 'San Fernando',
        'San Gabriel', 'San Marino', 'Santa Clarita', 'Santa Fe Springs',
        'Santa Monica', 'Sierra Madre', 'Signal Hill', 'South El Monte',
        'South Gate', 'South Pasadena', 'Temple City', 'Torrance', 'Vernon',
        'Walnut', 'West Covina', 'West Hollywood', 'Westlake Village',
        'Whittier',
    ],
    'Orange': [
        'Aliso Viejo', 'Anaheim', 'Brea', 'Buena Park', 'Costa Mesa', 'Cypress',
        'Dana Point', 'Fountain Valley', 'Fullerton', 'Garden Grove',
        'Huntington Beach', 'Irvine', 'La Habra', 'La Palma', 'Laguna Beach',
        'Laguna Hills', 'Laguna Niguel', 'Laguna Woods', 'Lake Forest',
        'Los Alamitos', 'Mission Viejo', 'Newport Beach', 'Orange', 'Placentia',
        'Rancho Santa Margarita', 'San Clemente', 'San Juan Capistrano',
        'Santa Ana', 'Seal Beach', 'Stanton', 'Tustin', 'Villa Park',
        'Westminster', 'Yorba Linda',
    ],
    'Riverside': [
        'Banning', 'Beaumont', 'Blythe', 'Calimesa', 'Canyon Lake',
        'Cathedral City', 'Coachella', 'Corona', 'Desert Hot Springs',
        'Eastvale', 'Hemet', 'Indian Wells', 'Indio', 'Jurupa Valley',
        'La Quinta', 'Lake Elsinore', 'Menifee', 'Moreno Valley', 'Murrieta',
        'Norco', 'Palm Desert', 'Palm Springs', 'Perris', 'Rancho Mirage',
        'Riverside', 'San Jacinto', 'Temecula', 'Wildomar',
    ],
    'San Bernardino': [
        'Adelanto', 'Apple Valley', 'Barstow', 'Big Bear Lake', 'Chino',
        'Chino Hills', 'Colton', 'Fontana', 'Grand Terrace', 'Hesperia',
        'Highland', 'Loma Linda', 'Montclair', 'Needles', 'Ontario',
        'Rancho Cucamonga', 'Redlands', 'Rialto', 'San Bernardino',
        'Twentynine Palms', 'Upland', 'Victorville', 'Yucaipa', 'Yucca Valley',
    ],
    'San Diego': [
        'Carlsbad', 'Chula Vista', 'Coronado', 'Del Mar', 'El Cajon',
        'Encinitas', 'Escondido', 'Imperial Beach', 'La Mesa', 'Lemon Grove',
        'National City', 'Oceanside', 'Poway', 'San Diego', 'San Marcos',
        'Santee', 'Solana Beach', 'Vista',
    ],
    'San Francisco': ['San Francisco'],
    'San Mateo': [
        'Atherton', 'Belmont', 'Brisbane', 'Burlingame', 'Colma', 'Daly City',
        'East Palo Alto', 'Foster City', 'Half Moon Bay', 'Hillsborough',
        'Menlo Park', 'Millbrae', 'Pacifica', 'Portola Valley', 'Redwood City',
        'San Bruno', 'San Carlos', 'San Mateo', 'South San Francisco',
        'Woodside',
    ],
    'Alameda': [
        'Alameda', 'Albany', 'Berkeley', 'Dublin', 'Emeryville', 'Fremont',
        'Hayward', 'Livermore', 'Newark', 'Oakland', 'Piedmont', 'Pleasanton',
        'San Leandro', 'Union City',
    ],
    'Contra Costa': [
        'Antioch', 'Brentwood', 'Clayton', 'Concord', 'Danville', 'El Cerrito',
        'Hercules', 'Lafayette', 'Martinez', 'Moraga', 'Oakley', 'Orinda',
        'Pinole', 'Pittsburg', 'Pleasant Hill', 'Richmond', 'San Pablo',
        'San Ramon', 'Walnut Creek',
    ],
    'Marin': [
        'Belvedere', 'Corte Madera', 'Fairfax', 'Larkspur', 'Mill Valley',
        'Novato', 'Ross', 'San Anselmo', 'San Rafael', 'Sausalito', 'Tiburon',
    ],
}


def norm(name):
    """Strip spacing, punctuation, and accents so city names match across files."""
    text = unicodedata.normalize('NFKD', str(name))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    for ch in [' ', '-', '.', "'", ',']:
        text = text.replace(ch, '')
    return text.lower()


city_to_cbsa = {
    norm(city): cbsa
    for cbsa, counties in CBSA_COUNTIES.items()
    for county in counties
    for city in COUNTY_CITIES[county]
}

population['CBSA'] = population['California_City'].apply(norm).map(city_to_cbsa)

population_cbsa = (
    population.dropna(subset=['CBSA'])
    .groupby('CBSA', as_index=False)['Population_Estimate_July1st_2024']
    .sum()
    .rename(columns={'Population_Estimate_July1st_2024': 'Population_2024'})
)

assert len(population_cbsa) == 4, "expected 4 CBSAs in the population rollup"

population_cbsa = population_cbsa.loc[population_cbsa.index.repeat(len(DATES_2024))].copy()
population_cbsa['Date'] = DATES_2024 * population_cbsa['CBSA'].nunique()

# ---- Permits: annual CBSA totals expanded to the six periods ----

permits['City_State'] = permits['City_State'].astype(str).str.strip()
permits_filtered = permits[permits['City_State'].isin(RELEVANT_CBSAS)].copy()

assert len(permits_filtered) == 4, "expected 4 CBSAs in the permits file"

permits_expanded = permits_filtered.rename(columns={
    'City_State': 'CBSA',
    'Total_Units': 'Permits_Total',
    '1_Unit': 'Permits_1_Unit',
    '2_Unit': 'Permits_2_Unit',
    '3_and_4_Unit': 'Permits_3_4_Units',
    '5_Units_Or_More': 'Permits_5plus',
    'Number_Of_Structure_With_5_Units_Or_More': 'Permits_5plus_Structures',
})
permits_expanded['Date'] = [DATES_2024] * len(permits_expanded)
permits_expanded = permits_expanded.explode('Date')

# ---- Merge onto the shared CBSA-month grid ----

for frame in [permits_expanded, population_cbsa, cpi_cbsa]:
    frame['CBSA'] = frame['CBSA'].astype(str).str.strip()
    frame['Date'] = frame['Date'].astype(str)

macro['Date'] = macro['Date'].astype(str)

master = (
    permits_expanded
    .merge(population_cbsa, on=['CBSA', 'Date'], how='left')
    .merge(cpi_cbsa, on=['CBSA', 'Date'], how='left')
    .merge(macro, on='Date', how='left')
)

assert len(master) == 24, f"expected 24 rows, got {len(master)}"
assert master['CPI'].notna().all(), "CPI has nulls after the merge"
assert master['HPI'].notna().all(), "HPI has nulls after the merge"

# ---- Derived metrics ----

for col in ['Population_2024', 'Permits_Total']:
    master[col] = (
        master[col].astype(str).str.replace(',', '', regex=False).astype(float)
    )

master['Permits_per_Capita'] = master['Permits_Total'] / master['Population_2024']
master['Permits_per_1000'] = master['Permits_per_Capita'] * 1000

master['Median_Income_2024'] = income['Median_Household_Income'].iloc[0]

master['CPI_pct_change'] = master.groupby('CBSA')['CPI'].pct_change(fill_method=None)

# Additional derived measures kept available for the Power BI table
master['HPI_pct_change'] = master.groupby('CBSA')['HPI'].pct_change(fill_method=None)
master['Mortgage30_pct_change'] = master.groupby('CBSA')['Mortgage_30yr'].pct_change(fill_method=None)
master['Unemp_pct_change'] = master.groupby('CBSA')['Unemployment_Rate'].pct_change(fill_method=None)

# Index-arithmetic columns. CPI and HPI use different base years, so these are
# not directly interpretable; index both to a common base before charting.
master['CPI_minus_HPI'] = master['CPI'] - master['HPI']
master['CPI_to_HPI'] = master['CPI'] / master['HPI']

for col in ['CPI', 'HPI', 'Mortgage_30yr', 'Unemployment_Rate', 'Permits_per_1000']:
    master[f'log_{col}'] = np.log(master[col].replace(0, np.nan))

master.to_csv("data/metro_cross_section_2024.csv", index=False)

print(master.shape)
print(sorted(master.columns))
print(master[['CBSA', 'Date', 'CPI', 'HPI', 'Permits_per_1000', 'Population_2024']])
print(master.isna().sum()[master.isna().sum() > 0])