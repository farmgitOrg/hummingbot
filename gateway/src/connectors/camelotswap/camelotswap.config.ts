import { ConfigManagerV2 } from '../../services/config-manager-v2';
import { AvailableNetworks } from '../../services/config-manager-types';

export namespace CamelotswapConfig {
  export interface ExchangeConfig {
    allowedSlippage: string;
    gasLimitEstimate: number;
    ttl: number;
    gasMultiplier: string;
    camelotswapRouterAddress: (chain: string, network: string) => string;
    tradingTypes: Array<string>;
    availableNetworks: Array<AvailableNetworks>;
  }

  export const config: ExchangeConfig = {
    allowedSlippage: ConfigManagerV2.getInstance().get(
      'camelotswap.allowedSlippage'
    ),
    gasLimitEstimate: ConfigManagerV2.getInstance().get(
      `camelotswap.gasLimitEstimate`
    ),
    ttl: ConfigManagerV2.getInstance().get('camelotswap.ttl'),
    gasMultiplier: ConfigManagerV2.getInstance().get(
      'camelotswap.gasMultiplier'
    ),
    camelotswapRouterAddress: (chain: string, network: string) =>
      ConfigManagerV2.getInstance().get(
        'camelotswap.contractAddresses.' +
          chain +
          '.' +
          network +
          '.camelotswapRouterAddress'
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
