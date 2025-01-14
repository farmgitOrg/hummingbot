jest.useFakeTimers();
import { SlingshotSwap } from '../../../../src/connectors/slingshotswap/slingshotswap';
import { patch, unpatch } from '../../../services/patch';
import { UniswapishPriceError } from '../../../../src/services/error-handler';
import {
    Fetcher,
    Pair,
    Percent,
    Route,
    Token,
    TokenAmount,
    Trade,
    TradeType,
} from '@pancakeswap/sdk'; //FIXME:
import { BigNumber } from 'ethers';
import { patchEVMNonceManager } from '../../../evm.nonce.mock';
import { Canto } from '../../../../src/chains/canto/canto';

let canto: Canto;
let slingshotswap: SlingshotSwap;

const ETH = new Token(
  7700,
  '0x5FD55A1B9FC24967C4dB09C513C3BA0DFa7FF687',
  18,
  'ETH'
);
const USDT = new Token(
  7700,
  '0xd567B3d7B8FE3C79a1AD8dA978812cfC4Fa05e75',
  6,
  'USDT'
);

beforeAll(async () => {
    canto = Canto.getInstance('mainnet');
    patchEVMNonceManager(canto.nonceManager);
    await canto.init();
    slingshotswap = SlingshotSwap.getInstance('Canto', 'mainnet');
    await slingshotswap.init();
  });

beforeEach(() => {
  patchEVMNonceManager(canto.nonceManager);
});

afterEach(() => {
  unpatch();
});

afterAll(async () => {
  await canto.close();
});

const patchFetchPairData = () => {
  patch(Fetcher, 'fetchPairData', () => {
    return new Pair(
        new TokenAmount(ETH, '2000000000000000000'),
        new TokenAmount(USDT, '100000000000')
    );
  });
};
const patchTrade = (key: string, error?: Error) => {
  patch(Trade, key, () => {
    if (error) return [];
    const ETH_USDT = new Pair(
        new TokenAmount(ETH, '2000000000000000000'),
        new TokenAmount(USDT, '100000000000')
    );
    const USDT_TO_ETH = new Route([ETH_USDT], USDT, ETH);
    return [
      new Trade(
        USDT_TO_ETH,
        new TokenAmount(USDT, '100000000000'),
        TradeType.EXACT_INPUT
      ),
    ];
  });
};

describe('verify SlingshotSwap estimateSellTrade', () => {
  it('Should return an ExpectedTrade when available', async () => {
    patchFetchPairData();
    patchTrade('bestTradeExactIn');

    const expectedTrade = await slingshotswap.estimateSellTrade(
      ETH,
      USDT,
      BigNumber.from(1)
    );
    expect(expectedTrade).toHaveProperty('trade');
    expect(expectedTrade).toHaveProperty('expectedAmount');
  });

  it('Should throw an error if no pair is available', async () => {
    patchFetchPairData();
    patchTrade('bestTradeExactIn', new Error('error getting trade'));

    await expect(async () => {
      await slingshotswap.estimateSellTrade(ETH, USDT, BigNumber.from(1));
    }).rejects.toThrow(UniswapishPriceError);
  });
});

describe('verify slingshotswap estimateBuyTrade', () => {
  it('Should return an ExpectedTrade when available', async () => {
    patchFetchPairData();
    patchTrade('bestTradeExactOut');

    const expectedTrade = await slingshotswap.estimateBuyTrade(
      ETH,
      USDT,
      BigNumber.from(1)
    );
    expect(expectedTrade).toHaveProperty('trade');
    expect(expectedTrade).toHaveProperty('expectedAmount');
  });

  it('Should return an error if no pair is available', async () => {
    patchFetchPairData();
    patchTrade('bestTradeExactOut', new Error('error getting trade'));

    await expect(async () => {
      await slingshotswap.estimateBuyTrade(ETH, USDT, BigNumber.from(1));
    }).rejects.toThrow(UniswapishPriceError);
  });
});

describe('verify slingshotswap Token List', () => {
  it('Should return a token by address', async () => {
    const token = slingshotswap.getTokenByAddress(
      '0x826551890Dc65655a0Aceca109aB11AbDbD7a07B' //WCANTO
    );
    expect(token).toBeInstanceOf(Token);
  });
});

describe('getAllowedSlippage', () => {
    it('return value of string when not null', () => {
      const allowedSlippage = slingshotswap.getAllowedSlippage('3/100');
      expect(allowedSlippage).toEqual(new Percent('3', '100'));
    });
  
    it('return value from config when string is null', () => {
      const allowedSlippage = slingshotswap.getAllowedSlippage();
      expect(allowedSlippage).toEqual(new Percent('1', '100'));
    });
  
    it('return value from config when string is malformed', () => {
      const allowedSlippage = slingshotswap.getAllowedSlippage('yo');
      expect(allowedSlippage).toEqual(new Percent('1', '100'));
    });
});