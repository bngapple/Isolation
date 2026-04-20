#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Net.Sockets;
using System.Text.RegularExpressions;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public static class BridgeState
    {
        public static double SizeMultiplier = 1.0;
        public static bool IsConnected = false;
        public static bool IsHalted = false;
        public static string LastReason = "offline";
    }

    public class PythonBridge : Strategy
    {
        private TcpClient tcpClient;
        private StreamReader reader;
        private StreamWriter writer;

        [NinjaScriptProperty]
        [Display(Name = "Bridge Host", GroupName = "Bridge", Order = 1)]
        public string BridgeHost { get; set; }

        [NinjaScriptProperty]
        [Range(1, 65535)]
        [Display(Name = "Bridge Port", GroupName = "Bridge", Order = 2)]
        public int BridgePort { get; set; }

        [NinjaScriptProperty]
        [Range(1, 50)]
        [Display(Name = "Contracts", GroupName = "Bridge", Order = 3)]
        public int Contracts { get; set; }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Dumb executor bridge: sends bars to Python and executes commands only.";
                Name = "PythonBridge";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                IsOverlay = false;
                BarsRequiredToTrade = 10;
                BridgeHost = "127.0.0.1";
                BridgePort = 5001;
                Contracts = 5;
            }
            else if (State == State.Configure)
            {
                AddDataSeries(BarsPeriodType.Minute, 1);
            }
            else if (State == State.Realtime)
            {
                EnsureConnected();
            }
            else if (State == State.Terminated)
            {
                DisconnectBridge();
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBars[0] < BarsRequiredToTrade || CurrentBars[1] < 0)
                return;

            if (!EnsureConnected())
            {
                FlattenForOffline();
                return;
            }

            if (BarsInProgress == 0)
            {
                HandleCommand(SendAndReceive(BuildBarMessage()));
                return;
            }

            if (BarsInProgress == 1)
            {
                HandleCommand(SendAndReceive(BuildMinuteMessage()));
            }
        }

        private bool EnsureConnected()
        {
            if (tcpClient != null && tcpClient.Connected && reader != null && writer != null)
                return true;

            DisconnectBridge();
            try
            {
                tcpClient = new TcpClient();
                tcpClient.Connect(BridgeHost, BridgePort);
                NetworkStream stream = tcpClient.GetStream();
                reader = new StreamReader(stream);
                writer = new StreamWriter(stream) { AutoFlush = true };
                BridgeState.IsConnected = true;
                BridgeState.LastReason = "connected";
                return true;
            }
            catch
            {
                DisconnectBridge();
                return false;
            }
        }

        private void DisconnectBridge()
        {
            BridgeState.IsConnected = false;
            BridgeState.IsHalted = false;
            BridgeState.SizeMultiplier = 1.0;
            BridgeState.LastReason = "offline";

            try
            {
                reader?.Dispose();
                writer?.Dispose();
                tcpClient?.Close();
            }
            catch
            {
            }

            reader = null;
            writer = null;
            tcpClient = null;
        }

        private string BuildBarMessage()
        {
            return string.Format(
                CultureInfo.InvariantCulture,
                "{{\"type\":\"bar\",\"ts\":{0},\"open\":{1},\"high\":{2},\"low\":{3},\"close\":{4},\"volume\":{5},\"position\":{6},\"daily_pnl\":{7},\"account\":\"{8}\"}}",
                new DateTimeOffset(Time[0]).ToUnixTimeSeconds(),
                Open[0],
                High[0],
                Low[0],
                Close[0],
                Volume[0],
                PositionValue(),
                GetDailyPnl(),
                AccountNameEscaped()
            );
        }

        private string BuildMinuteMessage()
        {
            double entryPrice = Position.MarketPosition == MarketPosition.Flat ? 0.0 : Position.AveragePrice;
            return string.Format(
                CultureInfo.InvariantCulture,
                "{{\"type\":\"minute\",\"ts\":{0},\"close\":{1},\"high\":{2},\"low\":{3},\"position\":{4},\"entry_price\":{5},\"daily_pnl\":{6}}}",
                new DateTimeOffset(Times[1][0]).ToUnixTimeSeconds(),
                Closes[1][0],
                Highs[1][0],
                Lows[1][0],
                PositionValue(),
                entryPrice,
                GetDailyPnl()
            );
        }

        private string SendAndReceive(string payload)
        {
            try
            {
                writer.WriteLine(payload);
                string line = reader.ReadLine();
                if (line == null)
                    throw new IOException("Python bridge disconnected");
                return line;
            }
            catch
            {
                DisconnectBridge();
                return "{\"action\":\"FLAT\",\"reason\":\"bridge_offline\"}";
            }
        }

        private void HandleCommand(string json)
        {
            string action = ExtractJsonString(json, "action");
            int commandContracts = ExtractJsonInt(json, "contracts", Contracts);
            double stop = ExtractJsonDouble(json, "stop", double.NaN);
            double target = ExtractJsonDouble(json, "target", double.NaN);
            double moveStopPrice = ExtractJsonDouble(json, "price", double.NaN);
            string reason = ExtractJsonString(json, "reason");

            if (string.Equals(action, "LONG", StringComparison.OrdinalIgnoreCase))
            {
                if (!double.IsNaN(stop))
                    SetStopLoss("Bridge", CalculationMode.Price, stop, false);
                if (!double.IsNaN(target))
                    SetProfitTarget("Bridge", CalculationMode.Price, target);
                if (Position.MarketPosition == MarketPosition.Flat)
                    EnterLong(commandContracts, "Bridge");
            }
            else if (string.Equals(action, "SHORT", StringComparison.OrdinalIgnoreCase))
            {
                if (!double.IsNaN(stop))
                    SetStopLoss("Bridge", CalculationMode.Price, stop, false);
                if (!double.IsNaN(target))
                    SetProfitTarget("Bridge", CalculationMode.Price, target);
                if (Position.MarketPosition == MarketPosition.Flat)
                    EnterShort(commandContracts, "Bridge");
            }
            else if (string.Equals(action, "FLAT", StringComparison.OrdinalIgnoreCase))
            {
                FlattenForReason(reason);
            }
            else if (string.Equals(action, "MOVE_STOP", StringComparison.OrdinalIgnoreCase))
            {
                if (!double.IsNaN(moveStopPrice) && Position.MarketPosition != MarketPosition.Flat)
                    SetStopLoss("Bridge", CalculationMode.Price, moveStopPrice, false);
            }
        }

        private void FlattenForOffline()
        {
            FlattenForReason("bridge_offline");
        }

        private void FlattenForReason(string reason)
        {
            string signalName = string.IsNullOrEmpty(reason) ? "BridgeFlat" : reason;
            if (Position.MarketPosition == MarketPosition.Long)
                ExitLong(signalName, "Bridge");
            else if (Position.MarketPosition == MarketPosition.Short)
                ExitShort(signalName, "Bridge");
        }

        private int PositionValue()
        {
            if (Position.MarketPosition == MarketPosition.Long)
                return 1;
            if (Position.MarketPosition == MarketPosition.Short)
                return -1;
            return 0;
        }

        private string AccountNameEscaped()
        {
            return Account != null ? Account.Name.Replace("\"", "\\\"") : "unknown";
        }

        private double GetDailyPnl()
        {
            double pnl = 0.0;
            DateTime today = Core.Globals.Now.Date;
            for (int i = 0; i < SystemPerformance.AllTrades.Count; i++)
            {
                Trade trade = SystemPerformance.AllTrades[i];
                if (trade == null)
                    continue;
                DateTime exitTime = trade.Exit != null ? trade.Exit.Time.Date : Core.Globals.MinDate;
                if (exitTime == today)
                    pnl += trade.ProfitCurrency;
            }
            return pnl;
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

        private int ExtractJsonInt(string json, string key, int fallback)
        {
            Match match = Regex.Match(json, string.Format("\"{0}\"\\s*:\\s*(?<value>-?\\d+)", Regex.Escape(key)));
            if (!match.Success)
                return fallback;

            int parsed;
            return int.TryParse(match.Groups["value"].Value, NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed)
                ? parsed
                : fallback;
        }
    }
}
