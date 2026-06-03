# Cheddar Mobile App

Cheddar is a mobile application that provides simultaneous grocery product search across multiple nearby stores and shows detailed price comparison to help minimize grocery costs for shoppers.

## Features

- **TODO**: Add features here

## Getting Started

Follow these instructions to get a copy of the project up and running on your system.

### Backend

The backend of the project is written in Python and provides a REST API for getting product results for a search term.

This is currently a developmental version, and will be integrated into the app itself in the future. To run the development version once it has been installed and set up properly on your machine, follow these steps:

1. Navigate to the project backend directory:
```bash
cd ...\\cheddar\\backend
```

2. Run the backend locally:
```bash
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8001
```
_Note: The port may be changed to any open port you may have_

The backend will now be running locally on your machine and be available for queries.