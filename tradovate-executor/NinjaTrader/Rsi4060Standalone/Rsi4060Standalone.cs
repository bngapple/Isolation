/*
 * Rsi4060Standalone.cs
 *
 * Native NinjaTrader 8 strategy for the current strongest RSI-only variant found in this repo:
 * - RSI period: 4
 * - Oversold: 40
 * - Overbought: 60
 * - No VWAP filter
 * - No ATR regime filter
 * - No Python bridge
 *
 * Intended runtime shape:
 * - Attach to an MNQ 15-minute chart
 * - Use a regular-hours session template that includes the 16:30 bar and 16:45 flatten window
 * - One position at a time
 * - Next-bar execution from OnBarClose signals
 *
 * Backtest-aligned defaults from the repo's RSI search:
 * - Stop: 10.0 points
 * - Target: 100.0 points
 * - Max hold: 5 bars
 * - Default quantity: 1 contract
 */

#region Using declarations
using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Text.RegularExpressions;
using System.Threading;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class Rsi4060Standalone : Strategy
    {
        private const string EntrySignalName = "RSI40_60";
        private const double OfflineBridgeSizeMultiplier = 0.34;

        private RSI rsi;
        private ATR atr;

        private DateTime entryTime = Core.Globals.MinDate;
        private bool beApplied;
        private bool pendingBE;
        private double entryPx;
        private int trailLevel;

        private double sessionPnL;
        private bool tradingHalted;
        private DateTime lastSessionDate = Core.Globals.MinDate;
        private int processedClosedTradeCount;

        private double[] atrBuffer;
        private int atrBufferIndex;
        private int atrBufferCount;
        private double rollingMedianAtr = double.NaN;

        private TcpClient tcpClient;
        private readonly string bridgeHost = "127.0.0.1";
        private bool bridgeHalted;
        private double bridgeSizeMultiplier = 1.0;
        private string bridgeMode = "NORMAL";
        private Thread bridgeThread;
        private volatile bool bridgeStopRequested;
        private readonly object bridgeLock = new object();
        private Random humanizerRng;
        private bool pendingEntry;
        private int pendingDirection;
        private DateTime entryAllowedAfter = Core.Globals.MinDate;

        [NinjaScriptProperty]
        [Range(1, 50)]
        [Display(Name = "Contracts", GroupName = "Parameters", Order = 1)]
        public int Contracts { get; set; }

        [NinjaScriptProperty]
        [Range(2, 20)]
        [Display(Name = "RSI Period", GroupName = "Parameters", Order = 2)]
        public int RsiPeriod { get; set; }

        [NinjaScriptProperty]
        [Range(1, 99)]
        [Display(Name = "Oversold", GroupName = "Parameters", Order = 3)]
        public double Oversold { get; set; }

        [NinjaScriptProperty]
        [Range(1, 99)]
        [Display(Name = "Overbought", GroupName = "Parameters", Order = 4)]
        public double Overbought { get; set; }

        [NinjaScriptProperty]
        [Range(1, 200)]
        [Display(Name = "Stop Loss Ticks", GroupName = "Risk", Order = 5)]
        public int StopLossTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, 2000)]
        [Display(Name = "Profit Target Ticks", GroupName = "Risk", Order = 6)]
        public int ProfitTargetTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, 50)]
        [Display(Name = "Max Hold Bars", GroupName = "Risk", Order = 7)]
        public int MaxHoldBars { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Session Start HHMMSS", GroupName = "Session", Order = 8)]
        public int SessionStartTime { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Last Entry HHMMSS", GroupName = "Session", Order = 9)]
        public int LastEntryTime { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Flatten HHMMSS", GroupName = "Session", Order = 10)]
        public int FlattenTime { get; set; }

        [NinjaScriptProperty]
        [Range(0, 60)]
        [Display(Name = "Break Even Minutes", GroupName = "Risk", Order = 11)]
        public int BreakEvenMinutes { get; set; }

        [NinjaScriptProperty]
        [Range(0.0, 100000.0)]
        [Display(Name = "Killswitch Dollar", GroupName = "Risk", Order = 12)]
        public double KillswitchDollar { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable ATR Filter", GroupName = "Filters", Order = 13)]
        public bool EnableAtrFilter { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Bridge", GroupName = "Bridge", Order = 14)]
        public bool EnableBridge { get; set; }

        [NinjaScriptProperty]
        [Range(1, 65535)]
        [Display(Name = "Bridge Port", GroupName = "Bridge", Order = 15)]
        public int BridgePort { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Trailing Stop", GroupName = "Risk", Order = 16)]
        public bool EnableTrailingStop { get; set; }

        [NinjaScriptProperty]
        [Range(1, 200)]
        [Display(Name = "Trail Step 1 Points", GroupName = "Risk", Order = 17)]
        public int TrailStep1Points { get; set; }

        [NinjaScriptProperty]
        [Range(1, 200)]
        [Display(Name = "Trail Step 2 Points", GroupName = "Risk", Order = 18)]
        public int TrailStep2Points { get; set; }

        [NinjaScriptProperty]
        [Range(1, 200)]
        [Display(Name = "Trail Step 3 Points", GroupName = "Risk", Order = 19)]
        public int TrailStep3Points { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Humanizer", GroupName = "Humanizer", Order = 20)]
        public bool EnableHumanizer { get; set; }

        [NinjaScriptProperty]
        [Range(1, 30)]
        [Display(Name = "Humanizer Min Seconds", GroupName = "Humanizer", Order = 21)]
        public int HumanizerMinSeconds { get; set; }

        [NinjaScriptProperty]
        [Range(1, 60)]
        [Display(Name = "Humanizer Max Seconds", GroupName = "Humanizer", Order = 22)]
        public int HumanizerMaxSeconds { get; set; }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description                  = "Native NinjaTrader port of the repo's RSI(4) 40/60 standalone strategy.";
                Name                         = "Rsi4060Standalone";
                Calculate                    = Calculate.OnBarClose;
                EntriesPerDirection          = 1;
                EntryHandling                = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                IsOverlay                    = false;
                BarsRequiredToTrade          = 10;

                Contracts         = 5;
                RsiPeriod         = 4;
                Oversold          = 40;
                Overbought        = 60;
                StopLossTicks     = 40;   // 10.0 points on MNQ
                ProfitTargetTicks = 400;  // 100.0 points on MNQ
                MaxHoldBars       = 5;
                SessionStartTime  = 93000;
                LastEntryTime     = 163000;
                FlattenTime       = 164500;
                BreakEvenMinutes  = 5;
                KillswitchDollar  = 750.0;
                EnableAtrFilter   = false;
                EnableBridge      = false;
                BridgePort        = 5001;
                EnableTrailingStop = false;
                TrailStep1Points   = 30;
                TrailStep2Points   = 50;
                TrailStep3Points   = 75;
                EnableHumanizer    = false;
                HumanizerMinSeconds = 1;
                HumanizerMaxSeconds = 5;
            }
            else if (State == State.Configure)
            {
                AddDataSeries(BarsPeriodType.Minute, 1);
                SetStopLoss(EntrySignalName, CalculationMode.Ticks, StopLossTicks, false);
                SetProfitTarget(EntrySignalName, CalculationMode.Ticks, ProfitTargetTicks);
            }
            else if (State == State.DataLoaded)
            {
                // Smooth = 1 keeps the output close to the simple signal form used in the Python search.
                rsi = RSI(Close, RsiPeriod, 1);
                atr = ATR(14);
                atrBuffer = Enumerable.Repeat(double.NaN, 50).ToArray();
                int accountHash = Account != null ? Account.Name.GetHashCode() : 0;
                humanizerRng = new Random(Math.Abs(Environment.MachineName.GetHashCode() ^ accountHash));
            }
            else if (State == State.Realtime)
            {
                processedClosedTradeCount = SystemPerformance.AllTrades.Count;
                if (EnableBridge)
                {
                    SetBridgeFallback();
                    StartBridgeThread();
                }
            }
            else if (State == State.Terminated)
            {
                StopBridgeThread();
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress == 1)
            {
                HandleBreakEvenSeries();
                HandleHumanizedEntry();
                return;
            }

            if (BarsInProgress != 0)
                return;

            if (CurrentBar < BarsRequiredToTrade || rsi == null || atr == null)
                return;

            ResetSessionStateIfNeeded(Time[0].Date);
            UpdateAtrMedian();

            int now = ToTime(Time[0]);

            // Flatten before looking for any new action so the strategy cannot carry overnight.
            if (Position.MarketPosition != MarketPosition.Flat && now >= FlattenTime)
            {
                FlattenOpenPosition("EOD");
                return;
            }

            if (tradingHalted)
                return;

            if (Position.MarketPosition != MarketPosition.Flat)
            {
                if (pendingBE)
                {
                    SetStopLoss(EntrySignalName, CalculationMode.Price, entryPx, false);
                    pendingBE = false;
                    beApplied = true;
                }

                int barsSinceEntry = BarsSinceEntryExecution(0, EntrySignalName, 0);
                if (barsSinceEntry >= 0 && barsSinceEntry >= MaxHoldBars)
                {
                    FlattenOpenPosition("MaxHold");
                }
                return;
            }

            if (pendingEntry && Position.MarketPosition == MarketPosition.Flat)
            {
                pendingEntry = false;
                pendingDirection = 0;
                entryAllowedAfter = Core.Globals.MinDate;
            }

            if (now < SessionStartTime || now >= LastEntryTime)
                return;

            double rsiValue = rsi[0];
            if (double.IsNaN(rsiValue))
                return;

            if (EnableAtrFilter)
            {
                if (atrBufferCount >= atrBuffer.Length)
                {
                    double atrValue = atr[0];
                    if (double.IsNaN(atrValue) || double.IsNaN(rollingMedianAtr) || atrValue <= rollingMedianAtr)
                        return;
                }
            }

            int direction = 0;
            if (rsiValue < Oversold)
                direction = 1;
            else if (rsiValue > Overbought)
                direction = -1;

            if (direction == 0)
                return;

            if (EnableHumanizer)
            {
                pendingEntry = true;
                pendingDirection = direction;
                entryAllowedAfter = DateTime.Now.AddSeconds(humanizerRng.Next(HumanizerMinSeconds, HumanizerMaxSeconds + 1));
                return;
            }

            SubmitEntry(direction);
        }

        protected override void OnExecutionUpdate(Execution execution, string executionId, double price, int quantity, MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution == null)
                return;

            if (execution.Order != null && string.Equals(execution.Order.Name, EntrySignalName, StringComparison.Ordinal))
            {
                if (marketPosition != MarketPosition.Flat)
                {
                    if (entryTime == Core.Globals.MinDate)
                        entryTime = time;
                    entryPx = execution.Order.AverageFillPrice > 0 ? execution.Order.AverageFillPrice : price;
                    beApplied = false;
                    pendingBE = false;
                    trailLevel = 0;
                    SetStopLoss(EntrySignalName, CalculationMode.Ticks, StopLossTicks, false);
                }
            }

            UpdateSessionPnLFromClosedTrades();

            if (!tradingHalted && KillswitchDollar > 0 && sessionPnL <= -KillswitchDollar)
            {
                tradingHalted = true;
                FlattenOpenPosition("Killswitch");
            }
        }

        protected override void OnPositionUpdate(Position position, double averagePrice, int quantity, MarketPosition marketPosition)
        {
            if (position == null || position.Account == null || Account == null || position.Account != Account)
                return;

            if (position.MarketPosition == MarketPosition.Flat)
            {
                entryTime = Core.Globals.MinDate;
                entryPx = 0.0;
                beApplied = false;
                pendingBE = false;
                trailLevel = 0;
                pendingEntry = false;
                pendingDirection = 0;
                entryAllowedAfter = Core.Globals.MinDate;
                SetStopLoss(EntrySignalName, CalculationMode.Ticks, StopLossTicks, false);
            }
        }

        private void FlattenOpenPosition(string reason)
        {
            if (Position.MarketPosition == MarketPosition.Long)
            {
                ExitLong(reason, EntrySignalName);
            }
            else if (Position.MarketPosition == MarketPosition.Short)
            {
                ExitShort(reason, EntrySignalName);
            }
        }

        private void HandleBreakEvenSeries()
        {
            if (CurrentBars[0] < BarsRequiredToTrade || CurrentBars[1] < 0)
                return;

            int activeBreakEvenMinutes = BreakEvenMinutes;
            if (EnableBridge)
            {
                lock (bridgeLock)
                {
                    if (string.Equals(bridgeMode, "DEFENSIVE", StringComparison.OrdinalIgnoreCase))
                        activeBreakEvenMinutes = 2;
                }
            }

            if (activeBreakEvenMinutes <= 0 || Position.MarketPosition == MarketPosition.Flat || entryPx <= 0.0 || entryTime == Core.Globals.MinDate)
                return;

            if ((Time[0] - entryTime).TotalMinutes < activeBreakEvenMinutes)
                return;

            if (!beApplied)
            {
                pendingBE = true;
                return;
            }

            if (!EnableTrailingStop || Position.MarketPosition == MarketPosition.Flat)
                return;

            double currentPrice = Close[0];
            double favorable = Position.MarketPosition == MarketPosition.Long
                ? currentPrice - entryPx
                : entryPx - currentPrice;

            if (favorable >= TrailStep3Points && trailLevel < 3)
            {
                double trailPrice = Position.MarketPosition == MarketPosition.Long ? entryPx + 50.0 : entryPx - 50.0;
                SetStopLoss(EntrySignalName, CalculationMode.Price, trailPrice, false);
                trailLevel = 3;
            }
            else if (favorable >= TrailStep2Points && trailLevel < 2)
            {
                double trailPrice = Position.MarketPosition == MarketPosition.Long ? entryPx + 30.0 : entryPx - 30.0;
                SetStopLoss(EntrySignalName, CalculationMode.Price, trailPrice, false);
                trailLevel = 2;
            }
            else if (favorable >= TrailStep1Points && trailLevel < 1)
            {
                double trailPrice = Position.MarketPosition == MarketPosition.Long ? entryPx + 15.0 : entryPx - 15.0;
                SetStopLoss(EntrySignalName, CalculationMode.Price, trailPrice, false);
                trailLevel = 1;
            }
        }

        private void HandleHumanizedEntry()
        {
            if (!EnableHumanizer || !pendingEntry || Position.MarketPosition != MarketPosition.Flat)
                return;

            if (DateTime.Now < entryAllowedAfter)
                return;

            if (SubmitEntry(pendingDirection))
            {
                pendingEntry = false;
                pendingDirection = 0;
                entryAllowedAfter = Core.Globals.MinDate;
            }
        }

        private bool SubmitEntry(int direction)
        {
            int liveContracts = Contracts;
            if (EnableBridge)
            {
                bool skipForBridge;
                lock (bridgeLock)
                {
                    skipForBridge = bridgeHalted;
                    liveContracts = Math.Max(1, (int)Math.Floor(Contracts * bridgeSizeMultiplier));
                }

                if (skipForBridge)
                    return false;
            }

            if (direction > 0)
                EnterLong(liveContracts, EntrySignalName);
            else if (direction < 0)
                EnterShort(liveContracts, EntrySignalName);
            else
                return false;

            return true;
        }

        private void ResetSessionStateIfNeeded(DateTime currentDate)
        {
            if (lastSessionDate == Core.Globals.MinDate || currentDate != lastSessionDate)
            {
                sessionPnL = 0.0;
                tradingHalted = false;
                lastSessionDate = currentDate;
                processedClosedTradeCount = SystemPerformance.AllTrades.Count;
            }
        }

        private void UpdateSessionPnLFromClosedTrades()
        {
            while (processedClosedTradeCount < SystemPerformance.AllTrades.Count)
            {
                Trade closedTrade = SystemPerformance.AllTrades[processedClosedTradeCount];
                sessionPnL += closedTrade.ProfitCurrency;
                processedClosedTradeCount++;
            }
        }

        private void UpdateAtrMedian()
        {
            double atrValue = atr[0];
            if (double.IsNaN(atrValue) || atrValue <= 0)
                return;

            atrBuffer[atrBufferIndex] = atrValue;
            atrBufferIndex = (atrBufferIndex + 1) % atrBuffer.Length;
            if (atrBufferCount < atrBuffer.Length)
                atrBufferCount++;

            rollingMedianAtr = RollingMedian(atrBuffer);
        }

        private double RollingMedian(double[] buffer)
        {
            double[] values = buffer.Where(v => !double.IsNaN(v)).ToArray();
            if (values.Length == 0)
                return double.NaN;

            Array.Sort(values);
            int mid = values.Length / 2;
            if (values.Length % 2 == 0)
                return (values[mid - 1] + values[mid]) / 2.0;

            return values[mid];
        }

        private void StartBridgeThread()
        {
            if (bridgeThread != null && bridgeThread.IsAlive)
                return;

            bridgeStopRequested = false;
            bridgeThread = new Thread(BridgeLoop)
            {
                IsBackground = true,
                Name = "IsolationBridgeClient"
            };
            bridgeThread.Start();
        }

        private void StopBridgeThread()
        {
            bridgeStopRequested = true;

            try
            {
                tcpClient?.Close();
            }
            catch
            {
            }

            if (bridgeThread != null && bridgeThread.IsAlive)
            {
                bridgeThread.Join(1000);
            }

            bridgeThread = null;
            tcpClient = null;
        }

        private void BridgeLoop()
        {
            while (!bridgeStopRequested)
            {
                try
                {
                    using (TcpClient client = new TcpClient())
                    {
                        tcpClient = client;
                        client.Connect(bridgeHost, BridgePort);

                        using (NetworkStream stream = client.GetStream())
                        using (StreamReader reader = new StreamReader(stream))
                        {
                            while (!bridgeStopRequested)
                            {
                                string line = reader.ReadLine();
                                if (line == null)
                                    break;

                                ApplyBridgeMessage(line);
                            }
                        }
                    }
                }
                catch
                {
                }

                SetBridgeFallback();

                if (!bridgeStopRequested)
                    Thread.Sleep(1000);
            }
        }

        private void ApplyBridgeMessage(string json)
        {
            string mode = ExtractJsonString(json, "mode");
            double sizeMultiplier = ExtractJsonDouble(json, "size_multiplier", OfflineBridgeSizeMultiplier);

            lock (bridgeLock)
            {
                bridgeMode = string.IsNullOrEmpty(mode) ? "NORMAL" : mode;
                bridgeSizeMultiplier = Math.Max(OfflineBridgeSizeMultiplier, sizeMultiplier);
                bridgeHalted = string.Equals(bridgeMode, "HALTED", StringComparison.OrdinalIgnoreCase);
            }
        }

        private void SetBridgeFallback()
        {
            lock (bridgeLock)
            {
                bridgeMode = "REDUCED";
                bridgeSizeMultiplier = OfflineBridgeSizeMultiplier;
                bridgeHalted = false;
            }
        }

        private string ExtractJsonString(string json, string key)
        {
            Match match = Regex.Match(json, string.Format("\"{0}\"\\s*:\\s*\"(?<value>[^\"]*)\"", Regex.Escape(key)));
            return match.Success ? match.Groups["value"].Value : string.Empty;
        }

        private double ExtractJsonDouble(string json, string key, double fallback)
        {
            Match match = Regex.Match(json, string.Format("\"{0}\"\\s*:\\s*(?<value>-?\\d+(?:\\.\\d+)?)", Regex.Escape(key)));
            if (!match.Success)
                return fallback;

            double parsed;
            return double.TryParse(match.Groups["value"].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out parsed)
                ? parsed
                : fallback;
        }

    }
}
