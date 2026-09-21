import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { SelectProps } from '@cloudscape-design/components/select';

import {
    Alert,
    Button,
    Container,
    ContentLayout,
    FormField,
    FormUI,
    Header,
    InputCSD,
    SelectCSD,
    SpaceBetween,
} from 'components';

import { useBreadcrumbs, useNotifications } from 'hooks';
import { ROUTES } from 'routes';
import { useImportVastInstanceMutation } from 'services/fleet';
import { useGetProjectsQuery } from 'services/project';

export const FleetImportVast: React.FC = () => {
    const navigate = useNavigate();
    const [pushNotification] = useNotifications();
    const { data: projectsResponse, isLoading: projectsLoading } = useGetProjectsQuery({ limit: 200 });
    const [importVastInstance, { isLoading: importing }] = useImportVastInstanceMutation();

    const [selectedProject, setSelectedProject] = useState<SelectProps.Option | null>(null);
    const [instanceId, setInstanceId] = useState('');
    const [fleetName, setFleetName] = useState('');

    useBreadcrumbs([
        { text: 'Fleets', href: ROUTES.FLEETS.LIST },
        { text: 'Import Vast.ai instance', href: ROUTES.FLEETS.IMPORT_VAST },
    ]);

    const projectOptions = useMemo<SelectProps.Options>(
        () =>
            (projectsResponse?.data ?? []).map((project) => ({
                label: project.project_name,
                value: project.project_name,
            })),
        [projectsResponse],
    );

    const parsedInstanceId = Number(instanceId);
    const canSubmit =
        !!selectedProject?.value &&
        Number.isInteger(parsedInstanceId) &&
        parsedInstanceId > 0 &&
        !importing &&
        !projectsLoading;

    const onSubmit = async (event: React.FormEvent) => {
        event.preventDefault();
        if (!canSubmit || !selectedProject?.value) {
            return;
        }

        try {
            const fleet = await importVastInstance({
                projectName: selectedProject.value,
                instance_id: parsedInstanceId,
                ...(fleetName.trim() ? { fleet_name: fleetName.trim() } : {}),
            }).unwrap();

            pushNotification({
                type: 'success',
                content: 'Vast.ai instance ' + parsedInstanceId + ' imported as fleet ' + fleet.name,
            });
            navigate(ROUTES.FLEETS.DETAILS.FORMAT(fleet.project_name, fleet.id));
        } catch (error) {
            const value = error as { data?: { error?: string; detail?: string }; error?: string };
            pushNotification({
                type: 'error',
                content: value.data?.error ?? value.data?.detail ?? value.error ?? 'Failed to import Vast.ai instance',
            });
        }
    };

    return (
        <ContentLayout header={<Header variant="h1">Import Vast.ai instance</Header>}>
            <form onSubmit={onSubmit}>
                <FormUI
                    actions={
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button formAction="none" onClick={() => navigate(ROUTES.FLEETS.LIST)} disabled={importing}>
                                Cancel
                            </Button>
                            <Button variant="primary" loading={importing} disabled={!canSubmit} formAction="submit">
                                Import
                            </Button>
                        </SpaceBetween>
                    }
                >
                    <SpaceBetween size="l">
                        <Alert type="info" header="Rent the machine in Vast.ai first">
                            This does not rent or terminate Vast.ai machines. Rent the machine in the Vast.ai Marketplace, then
                            paste its instance ID here. Jobs run directly inside the image already running on that Vast.ai
                            instance; dstack does not replace that image.
                        </Alert>

                        <Container header={<Header variant="h2">Existing Vast.ai instance</Header>}>
                            <SpaceBetween size="l">
                                <FormField label="Project" description="The dstack project that will own the imported fleet.">
                                    <SelectCSD
                                        selectedOption={selectedProject}
                                        options={projectOptions}
                                        loadingText="Loading projects"
                                        statusType={projectsLoading ? 'loading' : 'finished'}
                                        placeholder="Choose a project"
                                        filteringType="auto"
                                        onChange={({ detail }) => setSelectedProject(detail.selectedOption)}
                                    />
                                </FormField>

                                <FormField
                                    label="Vast.ai instance ID"
                                    description="The numeric instance/contract ID shown for the machine you already rented in Vast.ai."
                                >
                                    <InputCSD
                                        value={instanceId}
                                        inputMode="numeric"
                                        placeholder="e.g. 51788506"
                                        onChange={({ detail }) => setInstanceId(detail.value.replace(/[^0-9]/g, ''))}
                                    />
                                </FormField>

                                <FormField label="Fleet name" description="Optional. Defaults to vast-<instance-id>.">
                                    <InputCSD
                                        value={fleetName}
                                        placeholder={instanceId ? 'vast-' + instanceId : 'vast-<instance-id>'}
                                        onChange={({ detail }) => setFleetName(detail.value)}
                                    />
                                </FormField>
                            </SpaceBetween>
                        </Container>
                    </SpaceBetween>
                </FormUI>
            </form>
        </ContentLayout>
    );
};
