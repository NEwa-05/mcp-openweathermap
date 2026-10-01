from typing import Any
import httpx
import logging
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import Response, JSONResponse, RedirectResponse
from dotenv import load_dotenv
import os
from sentry_config import init_sentry

load_dotenv()
init_sentry()

open_weather_api_key = os.getenv("OPEN_WEATHER_API_KEY")
assert open_weather_api_key, "OPEN_WEATHER_API_KEY is not set"

# Set up logger
logger = logging.getLogger(__name__)

# Initialize MCP server (transport settings are passed to run() in MCP SDK v2)
mcp = MCPServer("weather", title="Weather")

# Constants
OPEN_WEATHER_GEO_URL = "https://api.openweathermap.org/geo/1.0/direct"
OPEN_WEATHER_WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
USER_AGENT = "weather-app/1.0"


async def open_weather_get(url: str, params: dict[str, Any]) -> Any | None:
    """Make a request to the OpenWeatherMap API with proper error handling.

    Returns the decoded JSON body, or None if the request fails. The request URL is never
    logged because it carries the API key.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    params = {**params, "appid": open_weather_api_key}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                url, params=params, headers=headers, timeout=30.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Weather API request failed with status {e.response.status_code}"
            )
        except Exception as e:
            logger.error(f"Weather API request failed: {type(e).__name__}")
        return None


async def get_lat_lon(city: str, state: str, country: str) -> tuple[float, float]:
    """Get the latitude and longitude for a city, state, and country."""
    data = await open_weather_get(
        OPEN_WEATHER_GEO_URL, {"q": f"{city},{state},{country}", "limit": 1}
    )
    if not data:
        logger.error(f"Failed to get location data for {city}, {state}, {country}")
        raise ValueError("Could not get location data")
    return float(data[0]["lat"]), float(data[0]["lon"])


@mcp.tool(
    title="Get current weather",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
)
async def get_current_weather(city: str, state: str, country: str) -> str:
    """
    Get the current weather for a location. If the city, state, or country are not provided, a reasonable guess should
    be made as to what the user is asking for.
    """
    logger.info(f"Weather request for {city}, {state}, {country}")

    try:
        lat, lon = await get_lat_lon(city, state, country)
    except Exception as e:
        logger.error(f"Error getting location for {city}, {state}, {country}: {e}")
        raise ToolError(
            f"Could not find a location matching {city}, {state}, {country}."
        )

    weather_data = await open_weather_get(
        OPEN_WEATHER_WEATHER_URL, {"lat": lat, "lon": lon, "units": "imperial"}
    )
    if not weather_data:
        raise ToolError("Unable to fetch weather data for this location.")

    try:
        weather_main = weather_data["weather"][0]["main"]
        weather_description = weather_data["weather"][0]["description"]
        weather_temp = weather_data["main"]["temp"]
        weather_feels_like = weather_data["main"]["feels_like"]
        weather_humidity = weather_data["main"]["humidity"]
    except (KeyError, IndexError, TypeError):
        logger.error(
            f"Unexpected weather response shape for {city}, {state}, {country}"
        )
        raise ToolError(f"Received unexpected weather data for {city}.")

    return f"The current weather is {weather_main} with a description of {weather_description}. The temperature is {weather_temp} degrees Fahrenheit. The feels like temperature is {weather_feels_like} degrees Fahrenheit. The humidity is {weather_humidity}%."


@mcp.custom_route("/", methods=["GET"])
async def redirect_to_github(request: Request) -> Response:
    return RedirectResponse(
        url="https://github.com/mcp-getgather/api-weather-mcp",
        status_code=301,
    )


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    logger.info("Starting weather MCP server")
    try:
        mcp.run(
            transport="streamable-http",
            host="0.0.0.0",
            port=8000,
            streamable_http_path="/mcp",
            stateless_http=True,
        )
    except Exception as e:
        logger.error(f"Failed to start server: {str(e)}")
        raise
