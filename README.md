# Fuel Route Optimizer API

API Django para cálculo de rotas otimizadas de combustível nos EUA | Django API for cost-optimized fuel routing across the USA.

## Overview / Visão Geral

Given a start and finish location within the United States, the API returns:

- Driving route geometry (GeoJSON)
- Optimal fuel stop recommendations based on retail prices
- Total estimated fuel cost (assuming 10 MPG and 500-mile vehicle range)

## Features

- Geocoding via Nominatim (OpenStreetMap)
- Routing via public OSRM
- Cost-aware fuel stop planning under range constraints
- Station data loaded from OPIS-style CSV
- Fast responses with minimal external API calls

## Requirements

- Python 3.12+
- See `requirements.txt`

## Setup

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations
python manage.py runserver
```

## API Endpoints

### Health Check
`GET /api/health/`

### Route Planner
`POST /api/route/`

```json
{
  "start": "Chicago, IL",
  "finish": "Dallas, TX"
}
```

Response includes route distance/duration, GeoJSON geometry, recommended fuel stops, and total cost breakdown.

## Tests

```bash
python manage.py test routing
```

## Notes

- Vehicle defaults: 500 mile range, 10 MPG (configurable in settings).
- Public OSRM and Nominatim are rate-limited; for production consider self-hosted instances and pre-geocoded stations.
- Station source data may lack precise coordinates; the planner uses price ranking and corridor logic.

---

**PT-BR**  
API que calcula a rota entre dois pontos nos EUA e indica os postos de combustível mais econômicos ao longo do caminho, respeitando o alcance máximo do veículo (500 milhas) e o consumo de 10 MPG. Utiliza dados de preços reais e serviços gratuitos de mapas/roteamento.
