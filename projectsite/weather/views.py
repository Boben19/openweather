from django.shortcuts import render
import os
import requests


def weather_view(request):
    api_key = os.getenv("OPENWEATHER_API_KEY")
    city = request.GET.get("city", "Puerto Princesa")
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        weather_data = {
            "city": data["name"],
            "country": data["sys"]["country"],
            "temp": data["main"]["temp"],
            "description": data["weather"][0]["description"].title(),
            "icon": data["weather"][0]["icon"],
        }
    except requests.exceptions.RequestException as e:
        weather_data = {
            "error": "Failed to fetch weather data",
            "details": str(e),
        }
    except KeyError:
        weather_data = {
            "error": f"Could not find weather information for '{city}'."
        }

    return render(request, "weather.html", {"weather": weather_data, "city": city})