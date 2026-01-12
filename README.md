# Nishi-Waseda Classroom Utilization Analyzer

A Python tool for analyzing classroom utilization at Waseda University's Nishi-Waseda Campus. This project collects class schedule data from the syllabus system and calculates utilization metrics (Dw and fw) to identify the least-busy classrooms.

## Features

- **Data Collection** (`collect.py`): Scrapes classroom schedule data from Waseda University's syllabus system using Playwright
- **Utilization Analysis** (`cal.py`): Computes classroom utilization metrics:
  - **Dw**: Number of occupied periods per week
  - **fw**: Utilization factor considering room capacity
- Identifies top-K least-busy classrooms per day
- Supports both Fall and Winter semester data

## Requirements

- Python 3.8+
- pandas
- beautifulsoup4
- lxml
- playwright

## Installation

1. Clone this repository:
```bash
git clone git@github.com:Yuhi0120/nishi-waseda-classroom-availability.git
cd nishi-waseda-classroom-availability
```

2. Install dependencies:
```bash
pip install pandas beautifulsoup4 lxml playwright
```

3. Install Playwright browsers:
```bash
playwright install
```

## Project Structure

```
CBD2/
├── cal.py                    # Main calculation script
├── collect.py                # Data collection script (web scraper)
├── data/
│   ├── room_capacity.csv     # Room capacity data
│   ├── period_room_fall/     # Fall semester schedule
│   │   ├── mon.csv
│   │   ├── tue.csv
│   │   ├── wed.csv
│   │   ├── thu.csv
│   │   └── fri.csv
│   └── period_room_winter/   # Winter semester schedule
│       ├── mon.csv
│       ├── tue.csv
│       ├── wed.csv
│       ├── thu.csv
│       └── fri.csv
└── README.md
```

## Usage

### 1. Collect Schedule Data (Optional)

If you need to update the schedule data from the syllabus system:

```bash
python collect.py
```

This will scrape the latest class schedule data and populate the CSV files in `data/period_room_fall/` and `data/period_room_winter/`.

### 2. Calculate Utilization Metrics

Run the main analysis script:

```bash
python cal.py
```

#### Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--data_dir` | `data` | Directory containing input data |
| `--out_dir` | `data/out` | Directory for output files |
| `--n_total` | `14` | Number of weeks in a semester (Ntotal) |
| `--topk` | `10` | Number of top least-busy rooms to display per day |

#### Example

```bash
python cal.py --topk 5 --out_dir results
```

### 3. Output Files

The script generates two CSV files in the output directory:

- **`dw_fw_all.csv`**: Complete utilization data for all classrooms
  - Columns: `day`, `classroom`, `capacity`, `occupied_periods`, `Nheld`, `Dw`, `fw`, `rank`

- **`topk_by_day.csv`**: Top-K least-busy classrooms per day
  - Columns: `day`, `rank`, `classroom`, `capacity`, `occupied_periods`, `Dw`, `fw`

## Metrics Explanation

| Metric | Formula | Description |
|--------|---------|-------------|
| **Dw** | `occupied_periods` | Number of periods the classroom is used per week |
| **fw** | `(Dw + 0.001) / capacity × 100000` | Utilization factor normalized by capacity (lower = less busy relative to size) |
| **Nheld** | `occupied_periods × Ntotal` | Total number of classes held in a semester |

## Data Format

### room_capacity.csv
```csv
classroom,capacity
52-102,164
52-103,164
...
```

### Schedule CSVs (mon.csv, tue.csv, etc.)
```csv
period,52-102,52-103,52-104,...
1,CLASS_NAME,...
2,CLASS_NAME,...
...
7,...
```

- Each row represents a period (1-7)
- Each column represents a classroom
- Non-empty cells indicate the classroom is occupied

## License

MIT License
