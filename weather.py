from typing import Any
import httpx
import logging
from mcp.server.mcpserver import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus, ToolsListChanged
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

# Fan-out bus behind `subscriptions/listen`. MCPServer serves `server/discover` and
# `subscriptions/listen` on its own; holding the bus lets us publish events to open streams.
subscription_bus = InMemorySubscriptionBus()

# Initialize MCP server (transport settings are passed to run() in MCP SDK v2)
mcp = MCPServer("weather", title="Weather", subscriptions=subscription_bus)

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


def location_query(city: str, state: str | None, country: str | None) -> str:
    """Build the geocoding query, leaving out the parts that were not provided."""
    return ",".join(
        part.strip() for part in (city, state, country) if part and part.strip()
    )


async def get_lat_lon(query: str) -> tuple[float, float]:
    """Get the latitude and longitude for a location query such as "Paris,FR"."""
    data = await open_weather_get(OPEN_WEATHER_GEO_URL, {"q": query, "limit": 1})
    if not data:
        logger.error(f"Failed to get location data for {query}")
        raise ValueError("Could not get location data")
    return float(data[0]["lat"]), float(data[0]["lon"])


@mcp.tool(
    title="Get current weather",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
)
async def get_current_weather(
    city: str, state: str | None = None, country: str | None = None
) -> str:
    """
    Get the current weather for a location. Only the city is required. Add the two-letter country code
    (for example "FR") when the city name is ambiguous, and the state (US locations only) to narrow it further.
    """
    query = location_query(city, state, country)
    logger.info(f"Weather request for {query}")

    try:
        lat, lon = await get_lat_lon(query)
    except Exception as e:
        logger.error(f"Error getting location for {query}: {e}")
        raise ToolError(f"Could not find a location matching {query}.")

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
        logger.error(f"Unexpected weather response shape for {query}")
        raise ToolError(f"Received unexpected weather data for {query}.")

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


if os.getenv("MCP_SPEC_DEBUG") == "1":
    # Spec-validation aid: lets you see an event arrive on an open `subscriptions/listen`
    # stream. Off by default; never enable on a publicly reachable instance.
    @mcp.custom_route("/debug/tools-list-changed", methods=["POST"])
    async def debug_tools_list_changed(request: Request) -> Response:
        await subscription_bus.publish(ToolsListChanged())
        return JSONResponse({"published": "toolsListChanged"})


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
