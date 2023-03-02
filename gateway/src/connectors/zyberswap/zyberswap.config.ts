import { ConfigManagerV2 } from '../../services/config-manager-v2';
import { AvailableNetworks } from '../../services/config-manager-types';

export namespace ZyberswapConfig {
  export interface ExchangeConfig {
    allowedSlippage: string;
    gasLimitEstimate: number;
    ttl: number;
    zyberswapRouterAddress: (chain: string, network: string) => string;
    tradingTypes: Array<string>;
    availableNetworks: Array<AvailableNetworks>;
  }

  export const config: ExchangeConfig = {
    allowedSlippage: ConfigManagerV2.getInstance().get(
      'zyberswap.allowedSlippage'
    ),
    gasLimitEstimate: ConfigManagerV2.getInstance().get(
      `zyberswap.gasLimitEstimate`
    ),
    ttl: ConfigManagerV2.getInstance().get('zyberswap.ttl'),
    zyberswapRouterAddress: (chain: string, network: string) =>
      ConfigManagerV2.getInstance().get(
        'zyberswap.contractAddresses.' +
          chain +
          '.' +
          network +
          '.zyberswapRouterAddress'
      ),
    tradingTypes: ['EVM_AMM'],
    availableNetworks: [
      {
        chain: 'ethereum',
        networks: ['arbitrum_one'],
      },
    ],
  };
}
