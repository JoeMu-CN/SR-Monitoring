import {useParams} from 'react-router-dom';
import type {ApiError} from './api';
import {CurrentRisksView} from './components/CurrentRisksView';
import {RiskDetailView} from './components/RiskDetailView';
import type {RiskItem} from './types';

interface RiskRouteViewProps {
  readonly riskItems: readonly RiskItem[];
  readonly onAskAssistant: (query: string) => void;
  readonly onCloseDetail: () => void;
  readonly onExportReport: (risk: RiskItem) => void;
  readonly onSelectRisk: (risk: RiskItem) => void;
  readonly onRequestError: (error: ApiError) => void;
}

export const RiskRouteView = ({riskItems, onAskAssistant, onCloseDetail, onExportReport, onSelectRisk, onRequestError}: RiskRouteViewProps) => {
  const {alertId} = useParams();
  if (alertId !== undefined) {
    return <RiskDetailView alertId={alertId} onAskAssistant={onAskAssistant} onClose={onCloseDetail} onExportReport={onExportReport} onRequestError={onRequestError} />;
  }

  return <CurrentRisksView riskItems={[...riskItems]} onSelectRisk={onSelectRisk} />;
};
