# Physical Commodity Arbitrage Engine

**Self-Hosted, Algorithmic Price Arbitrage Trading System**

A local Python-based commodity price arbitrage engine that monitors futures exchanges, spot markets, and OTC venues for mispricing opportunities. Executes automated trading strategies with portfolio risk management and live Streamlit dashboard.

## Overview

Physical Commodity Arbitrage Engine exploits market inefficiencies:

- **Multi-Exchange Monitoring**: Real-time price feeds from CME, Intercontinental Exchange (ICE), NYMEX, spot markets
- **Arbitrage Detection**: Automatic identification of price spreads (spatial, temporal, instrument-based)
- **Playwright Automation**: Scrape non-API exchange data and regulatory filings
- **Local Execution**: No external APIs—everything runs on your infrastructure
- **Risk Management**: Portfolio sizing, position limits, drawdown controls
- **Streamlit Dashboard**: Real-time profit/loss tracking, trade visualization
- **SQLite Audit Trail**: Full trade history for regulatory compliance and backtesting

## Architecture

### Components

1. **daemon.py** – Price monitoring and arbitrage detection loop
2. **Streamlit UI** – Live trading dashboard and analytics
3. **Playwright Automation** – Web scraping for non-API data sources
4. **SQLite Database** – Trade history and market data
5. **Configuration** – Exchange credentials, risk parameters, asset targets

## Key Features

### 1. **Multi-Venue Price Monitoring**

Monitor commodity prices across:
- **Futures Exchanges**: CME (corn, wheat, soybeans), NYMEX (crude oil, natural gas), ICE (sugar, cocoa)
- **Spot Markets**: Platts, S&P Global, local physical prices
- **OTC Venues**: Bilateral dealer quotes, ECNs
- **Crypto Commodities**: Bitcoin, Ethereum (emerging commodity markets)

### 2. **Arbitrage Detection Strategies**

| Strategy | Example |
|----------|---------|
| **Spatial** | Buy crude oil WTI, sell Brent at premium |
| **Temporal** | Buy Mar futures, sell Jun futures (carry trade) |
| **Instrument** | Physical commodity vs. futures contract mismatch |
| **Regional** | US natural gas vs. European gas price spread |
| **Cross-Commodity** | Crack spreads (crude → gasoline/diesel), crush spreads (soybeans → oil/meal) |

### 3. **Playwright Web Scraping**

Automated data extraction from:
- Exchange PDFs (settlement reports, volume data)
- Regulatory filings (CFTC commitments of traders)
- Broker websites (bid/ask quotes)
- Spot market platforms (non-standardized venues)

### 4. **Risk Management**

Portfolio controls:
- Position size limits (% of account)
- Daily loss limits (stop-loss triggers)
- Leverage caps (margin requirements)
- Concentration limits (max % in single commodity)
- Maximum drawdown thresholds

### 5. **Streamlit Dashboard**

Real-time monitoring:
- P&L by commodity
- Active positions with entry/exit prices
- Trade history with execution details
- Risk metrics (Sharpe ratio, max drawdown, win rate)
- Spread visualization charts

## Installation

### Prerequisites

- Linux (Ubuntu 20.04+, Arch)
- Python 3.10+
- uv package manager
- Chrome/Chromium (for Playwright)
- ~5GB disk space

### System Setup

```bash
# Install Playwright dependencies
sudo apt update
sudo apt install -y \
  chromium-browser \
  chromium-chromedriver \
  python3-pip

# Clone repository
git clone https://github.com/malachi-666/commodity-arbitrage-engine.git
cd commodity-arbitrage-engine

# Install Python dependencies
uv pip install -e .

# Download Playwright browsers
playwright install chromium
```

### Configuration

1. **Create config file** (`config.yaml`):

```yaml
exchanges:
  cme:
    enabled: true
    instruments:
      - symbol: "ZC"      # Corn
        margin: 1100      # USD per contract
      - symbol: "ZS"      # Soybeans
        margin: 1400
  
  nymex:
    enabled: true
    instruments:
      - symbol: "CL"      # Crude oil
        margin: 6000
      - symbol: "NG"      # Natural gas
        margin: 1200

risk_management:
  account_size_usd: 100000
  max_position_percent: 2      # Max 2% of account per trade
  daily_loss_limit: 5000       # Stop if daily loss > $5k
  max_leverage: 1.5
  max_drawdown: 10             # Max 10% peak-to-valley

arbitrage:
  min_spread_percent: 0.5      # Only trade spreads >0.5%
  max_holding_period_days: 30
  slippage_assumption: 0.1     # 0.1% execution slippage

database:
  path: ~/.commodity_engine/trades.db
  retention_days: 730          # 2 years history
```

2. **Set Exchange API Keys**:

```bash
export CME_API_KEY="your_cme_key"
export ICE_API_KEY="your_ice_key"
export NYMEX_API_KEY="your_nymex_key"

# Or save to .env
echo "CME_API_KEY=..." >> ~/.commodity_engine/.env
```

## Quick Start

### 1. Start Price Monitoring Daemon

```bash
# Run background price monitor
python daemon.py --config config.yaml --background

# Output:
# [2026-05-09 14:30:22] Initialized 6 exchange connections
# [2026-05-09 14:30:25] CME: ZC (Corn) $465.25/bu (Volume: 150k)
# [2026-05-09 14:30:26] NYMEX: CL (Crude) $78.50/bbl (Volume: 890k)
# [2026-05-09 14:30:30] Arbitrage Alert: ZS-ZM spread +0.85% (>0.5% threshold)
# [2026-05-09 14:30:31] Executing BUY 5x ZS / SELL 15x ZM (crush spread)
```

### 2. Monitor Dashboard

```bash
# Launch Streamlit UI
streamlit run dashboard.py

# Opens: http://localhost:8501
```

**Dashboard displays:**
- Real-time portfolio value
- Active positions with mark-to-market P&L
- Today's realized trades
- Risk metrics (Sharpe, max drawdown, win rate)
- Spread visualization charts

### 3. View Trade History

```bash
# Query recent trades
python -m commodity_engine.analyzer --history --limit 50

# Output:
# Trade ID | Entry Date    | Commodity | Position | Entry Price | Current | P&L (%)
# 1001     | 2026-05-08    | ZS/ZM     | BUY 5x   | $465.10     | $466.50 | +0.30%
# 1002     | 2026-05-08    | CL/RB     | BUY 2x   | $78.40      | $78.55  | +0.19%
# 1003     | 2026-05-07    | NG/HH     | SELL 10x | $2.85       | $2.82   | +1.05%

# Export to CSV
python -m commodity_engine.analyzer --export trades.csv
```

### 4. Configure Specific Strategies

#### Strategy: Crush Spread (Soybean Oil/Meal)

```python
# strategies/crush_spread.py
class CrushSpread(ArbitrageStrategy):
    """Buy soybeans, sell oil + meal component"""
    
    def identify_opportunity(self):
        soy_price = self.get_price("ZS")      # Soybeans
        oil_price = self.get_price("ZL")      # Soybean oil
        meal_price = self.get_price("ZM")     # Soybean meal
        
        # Calculate crack margin
        crush_margin = soy_price - (oil_price * 1.1 + meal_price * 0.9)
        
        if crush_margin > self.config["min_spread_percent"]:
            return {
                "buy": [("ZS", 5)],      # Buy 5 soybeans
                "sell": [("ZL", 5), ("ZM", 15)],  # Sell equivalent oil + meal
                "expected_profit": crush_margin,
                "strategy": "crush_spread"
            }
```

#### Strategy: Calendar Spread (Contract Month Arbitrage)

```python
class CalendarSpread(ArbitrageStrategy):
    """Exploit futures contract month differences"""
    
    def identify_opportunity(self):
        front_month = self.get_price("CL_June")
        back_month = self.get_price("CL_July")
        
        spread = abs(front_month - back_month) / front_month
        
        if spread > self.config["min_spread_percent"]:
            return {
                "buy": [("CL_June", 10)],
                "sell": [("CL_July", 10)],
                "expected_profit": spread,
                "strategy": "calendar_spread"
            }
```

## Usage

### CLI Commands

```bash
# Start daemon
python daemon.py start

# Monitor specific commodity
python daemon.py monitor --commodity ZS

# Execute test trade (no real money)
python daemon.py backtest --strategy crush_spread --period 30d

# Generate performance report
python daemon.py report --period month

# Show positions
python daemon.py positions

# Close all positions
python daemon.py flatten --confirm
```

### Streamlit Dashboard Commands

```bash
# Start dashboard
streamlit run dashboard.py

# Custom port
streamlit run dashboard.py --server.port 8502

# Remote access
streamlit run dashboard.py --server.address 0.0.0.0
```

### Configuration Tuning

```yaml
# Conservative mode (lower risk, fewer trades)
arbitrage:
  min_spread_percent: 1.5      # Only >1.5% spreads
  max_position_percent: 1.0    # Smaller positions
  daily_loss_limit: 2000       # Tighter loss control

# Aggressive mode (higher risk, more trades)
arbitrage:
  min_spread_percent: 0.1      # Even thin spreads
  max_position_percent: 5.0    # Larger positions
  daily_loss_limit: 20000      # Higher loss tolerance
```

## Architecture

```
Physical Commodity Arbitrage Engine
├── daemon.py
│   ├── Exchange Connectors
│   │   ├── CME API
│   │   ├── ICE API
│   │   └── NYMEX API
│   ├── Price Monitor Loop
│   │   ├── Fetch Real-Time Quotes
│   │   ├── Detect Arbitrage Spreads
│   │   └── Execute Trades
│   └── Position Manager
│       ├── Track Open Positions
│       ├── Calculate P&L
│       └── Enforce Risk Limits
│
├── Strategies/
│   ├── crush_spread.py (Soybean oil/meal)
│   ├── crack_spread.py (Crude → gasoline/diesel)
│   ├── calendar_spread.py (Contract month)
│   └── spatial_arbitrage.py (Geographic spread)
│
├── dashboard.py (Streamlit UI)
│   ├── Portfolio Summary
│   ├── Position Monitor
│   ├── Trade History
│   ├── Risk Metrics
│   └── Strategy Analysis
│
├── scraper.py (Playwright Automation)
│   ├── PDF Parsing (CFTC reports)
│   ├── Web Scraping (Broker quotes)
│   └── Non-API Data Collection
│
└── database/
    ├── trades.db
    ├── market_data.db
    └── position_log.db
```

## Performance & Backtesting

### Historical Performance

```bash
# Backtest crush spread strategy (past 6 months)
python daemon.py backtest \
  --strategy crush_spread \
  --period 6m \
  --starting_capital 100000

# Output:
# Period: 2025-11-09 to 2026-05-09
# Starting Capital: $100,000
# Ending Capital: $123,450
# Total Return: +23.45%
# Sharpe Ratio: 1.85
# Max Drawdown: -5.2%
# Win Rate: 68%
# Trades Executed: 47
```

### Live Performance Tracking

Dashboard shows:
- Daily P&L (realized + unrealized)
- Monthly returns
- Correlation with commodity indices
- Position duration distribution

## Risk Management

### Automated Controls

1. **Position Size Limits**:
   - Max 2% account per single arbitrage
   - Max 10% account in single commodity
   - Max 50% account in correlated spreads

2. **Daily Loss Limits**:
   - Stop trading if daily realized loss > $5,000
   - Auto-flatten if max drawdown > 10%

3. **Margin Requirements**:
   - Maintain 2x minimum margin (safety buffer)
   - Alert if margin ratio < 2.0x

4. **Execution Controls**:
   - Assume 0.1% slippage on all trades
   - Require >0.5% spread after slippage

## Troubleshooting

### "Cannot connect to exchange API"

```bash
# Verify credentials
echo $CME_API_KEY

# Test connection
python -c "from commodity_engine.exchanges import CME; print(CME().ping())"

# Check IP whitelisting on exchange
```

### "Dashboard shows no data"

```bash
# Ensure daemon is running
ps aux | grep daemon.py

# Check database
sqlite3 ~/.commodity_engine/trades.db ".tables"

# Restart dashboard
streamlit run dashboard.py --logger.level=debug
```

### "High slippage on trades"

```yaml
# Adjust execution parameters
arbitrage:
  slippage_assumption: 0.15    # Increase to 0.15%
  min_spread_percent: 1.0      # Only larger spreads
  order_type: "limit"          # vs "market"
```

## Advanced Usage

### Custom Strategy Development

Create new strategy:

```python
# strategies/my_strategy.py
from commodity_engine.strategies import ArbitrageStrategy

class MyStrategy(ArbitrageStrategy):
    def identify_opportunity(self):
        # Your logic here
        pass
    
    def execute(self, opportunity):
        # Execution logic
        pass
```

Register in config:

```yaml
strategies:
  - name: "crush_spread"
    enabled: true
  - name: "my_strategy"
    enabled: true
    parameters:
      threshold: 0.5
```

### Integration with External Data

```python
# Import alternative data sources
from commodity_engine.data import CRUDEExtractor, WeatherData

class EnhancedStrategy(ArbitrageStrategy):
    def identify_opportunity(self):
        crude_inventory = CRUDEExtractor().weekly_inventory()
        weather = WeatherData().farm_conditions()
        
        # Combine with price data for better signals
```

## Legal & Compliance

- **Market Regulations**: Comply with CFTC position limits
- **Tax Reporting**: Track cost basis for accurate tax filing
- **Audit Trail**: All trades logged with entry/exit rationale
- **Slippage & Fees**: Account for all costs in profitability calculations

## Contributing

1. Add new exchange integrations
2. Develop novel arbitrage strategies
3. Optimize trade execution algorithms
4. Improve Playwright data extraction

## License

See LICENSE file.

## Disclaimer

Commodity trading involves substantial risk of loss. Past performance is not indicative of future results. This engine is for experienced traders only. Always:

- Backtest thoroughly before live trading
- Start with small position sizes
- Monitor positions actively
- Maintain adequate risk controls

---

**Physical Commodity Arbitrage Engine**: Exploit market inefficiencies algorithmically.
