# Kasa-Nice

A modern, containerized desktop GUI application for controlling TP-Link Kasa smart home devices in your local network.

Built with Python using the [python-kasa](https://github.com/python-kasa/python-kasa) library and [NiceGUI](https://github.com/zauberzeug/nicegui) framework.

![kasa-nice screenshot](Kasa_GUI_Screenshot.png?raw=True)

## Features

- 🏠 **Local Network Control**: Discover and control Kasa devices on your local network (no cloud required)
- 🎨 **Modern Web UI**: Clean, responsive interface accessible via web browser
- 🐳 **Docker Support**: Easy deployment with Docker and Docker Compose
- 📊 **Usage Monitoring**: View energy consumption charts for compatible devices
- 🔧 **Device Management**: Control switches, dimmers, color bulbs, and light strips
- 📱 **Cross-Platform**: Works on Windows, macOS, and Linux
- 📝 **Structured Logging**: Comprehensive logging with rotation

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/ryandavila/Kasa-Nice.git
cd Kasa-Nice

# Run with Docker Compose
docker compose up -d

# Access the web interface at http://localhost:8080
```

### Option 2: uv Installation (Recommended for Python)

```bash
# Clone the repository
git clone https://github.com/ryandavila/Kasa-Nice.git
cd Kasa-Nice

# Install dependencies with uv (much faster than pip)
uv sync

# Run the application
uv run python main.py
```

## Project Structure

```
├── main.py              # Main application entry point (was kasa_main_GUI.py)
├── usage.py             # Usage monitoring and plotting (was kasa_nice_usage.py)
├── logging_config.py    # Logging configuration
├── static/              # Static assets (images, etc.)
├── pyproject.toml       # Python project configuration
└── Dockerfile          # Docker containerization
```

## Configuration

### Environment Variables

- `KASA_HOST`: Host to bind the web server (default: `127.0.0.1`)
- `KASA_PORT`: Port for the web server (default: `8080`)

### Docker Environment

When running with Docker, the application uses `host` networking mode to discover devices on your local network. Logs are persisted to the `./logs` directory.

## Usage

1. **Device Discovery**: The application automatically discovers Kasa devices on your network
2. **Manual Discovery**: Use the Discovery tab to search for specific devices by IP address
3. **Device Control**:
   - Toggle devices on/off
   - Adjust brightness for dimmable devices
   - Change colors for color-capable bulbs
   - Set effects for light strips
4. **Usage Monitoring**: View energy consumption data in the Usage tab (for devices with energy monitoring)

## Supported Devices

All devices supported by the python-kasa library:

### Plugs

- HS100, HS103, HS105, HS107, HS110
- KP105, KP115, KP125, KP401
- EP10

### Power Strips

- EP40, HS300, KP303
- KP200 (in wall), KP400, KP405 (dimmer)

### Wall Switches

- ES20M, HS200, HS210, HS220
- KS200M, KS220M, KS230

### Bulbs

- LB100, LB110, LB120, LB130, LB230
- KL50, KL60, KL110, KL120, KL125, KL130, KL135

### Light Strips

- KL400, KL420, KL430

## Development

### Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (recommended for fast dependency management)
- Docker (optional)

### Local Development

```bash
# Install dependencies with uv (recommended)
uv sync --dev

# Run in development mode
uv run python main.py
```

## Logging

The application includes structured logging with:

- Console output for real-time monitoring
- File-based logging with rotation (10MB max, 5 backups)
- Logs stored in `./logs/kasa_nice.log`

## Troubleshooting

### Device Discovery Issues

- Ensure your devices are on the same network segment
- Try manual discovery using the device's IP address
- Check that devices are powered on and connected to WiFi

### Docker Network Issues

- The application uses `host` networking mode for device discovery
- Ensure Docker has access to your local network
- On some systems, you may need to adjust firewall settings

### Permission Issues

- Ensure the logs directory is writable
- On Linux/macOS, you may need to adjust file permissions

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is licensed under the terms specified in the LICENSE file.

## Credits

This project is a fork and modernization of the original [Kasa-Nice](https://github.com/uni-byte/Kasa-Nice) by [uni-byte](https://github.com/uni-byte). The original project provided the foundation and core functionality for controlling TP-Link Kasa devices through a web interface.

### Original Author

- **uni-byte** - Original creator and maintainer of Kasa-Nice

### Fork Enhancements

This fork includes various improvements:

- Modern Python packaging with `pyproject.toml`
- uv-based dependency management
- Simplified file structure for Docker webapp deployment
- Enhanced code quality with ruff linting and formatting
- Docker containerization improvements
- Updated dependencies and Python 3.14 support

## Acknowledgments

- [python-kasa](https://github.com/python-kasa/python-kasa) - TP-Link Kasa device control
- [NiceGUI](https://github.com/zauberzeug/nicegui) - Modern web UI framework
- [Plotly](https://plotly.com/) - Interactive charts and graphs

## Links

- [GitHub Repository](https://github.com/ryandavila/Kasa-Nice)
- [NiceGUI Documentation](https://nicegui.io/)
- [Python-Kasa Documentation](https://python-kasa.readthedocs.io/)

