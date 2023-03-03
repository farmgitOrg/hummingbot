import { ConfigManagerV2 } from '../../services/config-manager-v2';
import { AvailableNetworks } from '../../services/config-manager-types';

export namespace SwapfishConfig {
  export interface ExchangeConfig {
    allowedSlippage: string;
    gasLimitEstimate: number;
    ttl: number;
    gasMultiplier: string;
    swapfishRouterAddress: (chain: string, network: string) => string;
    tradingTypes: Array<string>;
    availableNetworks: Array<AvailableNetworks>;
  }

  export const config: ExchangeConfig = {
    allowedSlippage: ConfigManagerV2.getInstance().get(
      'swapfish.allowedSlippage'
    ),
    gasLimitEstimate: ConfigManagerV2.getInstance().get(
      `swapfish.gasLimitEstimate`
    ),
    ttl: ConfigManagerV2.getInstance().get('swapfish.ttl'),
    gasMultiplier: ConfigManagerV2.getInstance().get('swapfish.gasMultiplier'),
    swapfishRouterAddress: (chain: string, network: string) =>
      ConfigManagerV2.getInstance().get(
        'swapfish.contractAddresses.' +
          chain +
          '.' +
          network +
          '.swapfishRouterAddress'
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
